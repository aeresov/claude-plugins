#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-only
"""Root-side helper of the OpenVPN 2 backend: starts, stops and reports tunnels run by OpenVPN 2.6 in transient systemd units.

OpenVPN 2 has no unprivileged service to ask for a tunnel the way openvpn3-linux has its D-Bus services, so starting one
needs root. The MCP server stays unprivileged and calls this file through ``sudo -n`` for the two things that need it:

    sudo -n ovpn2_helper.py up NAME [--byo]      profile on stdin, or /etc/openvpn/client/NAME.conf with --byo
    sudo -n ovpn2_helper.py down NAME

and without sudo for the read-only ones (``status [NAME]``, and ``probe``, which doctor and setup run: it reports the
OpenVPN version and asks ``sudo -n -l`` whether the helper may run as root — a question, nothing is executed). Each
prints one JSON object on stdout.
Exit status: 0 done, 1 refused or failed (the JSON says why), 2 bad usage.

This file is the trust boundary when sudoers allows only this helper, so everything that decides what root does is here:

- The profile is an allowlist. OpenVPN runs ``up``/``plugin``/... as root and ``log``/``status``/... overwrite files as
  root, so a profile is parsed with OpenVPN's own tokenizing rules, every directive must be on ``_DIRECTIVES``, and a fresh
  profile is written from the parsed tokens: what OpenVPN reads is what was vetted. No scripts run at all.
- OpenVPN never changes the routing table (--route-noexec). The helper reads which prefixes the server pushed and adds
  the private ones itself, bound to the tunnel device, so every route this backend causes disappears with the device —
  also after a hard kill. Pushed options are default-deny: only the few a split tunnel needs get through, so no pushed
  redirect-gateway, DNS or IPv6 option reaches OpenVPN however it is spelled. The one thing the server still decides is
  the tunnel's own address: its route is moved below the host's own routes, a network it would carve out of one the host
  is attached to is refused, and IPv6 is switched off on the device (a router advertisement sent down the tunnel later
  would otherwise hand the host an IPv6 default route). After that the routes to this host's gateway, DNS servers and
  metadata address are checked against how they looked before; a tunnel that changed any of them is torn down. On a
  machine reached only over the network that is the difference between a failed connect and a lost machine.
- DNS is left alone: OpenVPN 2.6 does nothing with pushed DNS unless a script is configured, and none is.
- A tunnel never reconnects by itself and never outlives its idle or lifetime limit, so one orphaned by a crash goes away.
- Connect is commit-confirm: a systemd timer that stops the tunnel is armed before it starts and cancelled only once the
  route check has passed, so a tunnel that did cut the host off is gone two minutes later even if this process died.

Standard library only, Python 3.9 syntax (Amazon Linux 2023's system python3), no package-relative imports: it runs by
path as root, outside the server's venv. ``tests/test_ovpn2_helper.py`` guards all three.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

# Bumped when the command line or the JSON changes shape. The server also refuses an installed copy that differs from the
# bundled file at all (`helper_state`), so this is for a human comparing two copies.
PROTOCOL = 1

OPENVPN = "/usr/sbin/openvpn"
RUN_DIR = Path("/run/openvpn3-on-demand")
BYO_DIR = Path("/etc/openvpn/client")
UNIT_PREFIX = "openvpn3-on-demand-"
# Not under UNIT_PREFIX: the rollback units must not show up as tunnels.
ROLLBACK_PREFIX = "openvpn3-od-rollback-"
INSTALLED_PATH = Path("/usr/local/libexec/openvpn3-on-demand/ovpn2-helper")
ROOT_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
PROC_STATUS = Path("/proc/self/status")

# 2.6 is the first release that keeps CAP_NET_ADMIN after dropping to an unprivileged user and that has --session-timeout.
# 2.7 runs a DNS up/down script by default, which this backend has not been tested against, so it is refused, not guessed at.
SUPPORTED_VERSIONS = ((2, 6),)

CONNECTED_STATUS = "Initialization Sequence Completed"

# How long `up` waits for the tunnel. The openvpn limits below end a failing attempt well inside it.
CONNECT_TIMEOUT_SECS: float = 60.0
# When the rollback timer fires unless `up` got as far as cancelling it: after the connect deadline, with room to spare.
ROLLBACK_SECS = int(CONNECT_TIMEOUT_SECS) + 60
# Dead-man limits: less than INACTIVE_BYTES of tunnel traffic in INACTIVE_SECS (a byte floor, because the kernel's own
# IPv6 router solicitations on the device would otherwise count as activity for hours), and total lifetime in seconds
# (systemd stops the unit after it).
INACTIVE_SECS = 3600
INACTIVE_BYTES = 8192
MAX_LIFETIME_SECS = 12 * 3600
# Above any metric a distribution gives its own routes (Amazon Linux: 512, NetworkManager: 100-600), so on an equal
# prefix the host's existing route wins over the tunnel's. Every IPv4 route on a tunnel device carries it: the pushed
# ones, and the one the kernel makes for the tunnel's own address (created at metric 0, which outranks them all).
ROUTE_METRIC = 5000
DEVICE_PREFIX = "ovod"
# Per-device IPv6 switches; absent on a host booted without IPv6.
IPV6_CONF = Path("/proc/sys/net/ipv6/conf")

RESOLV_CONFS = (Path("/etc/resolv.conf"), Path("/run/systemd/resolve/resolv.conf"))
# Cloud metadata endpoint (EC2, GCE, Azure, OpenStack): an instance that loses its route to it loses its credentials.
METADATA_ADDRESS = "169.254.169.254"

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))

# All of these are applied with fullmatch(): "$" would also match before a trailing newline, and a name ending in one
# splits the unit's ExecStopPost path in two.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_BARE_TOKEN = re.compile(r"[A-Za-z0-9._:/@%+=,*-]+")
_HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_ALGO = re.compile(r"[A-Za-z0-9:_.+-]{1,256}")
_INLINE_TAG = re.compile(r"<([a-z0-9-]+)>")
# What a directive name looks like. A refusal quotes the offending line's first word only when it is one: a profile with
# a missing <key> tag would otherwise put a line of key material into the error, and so into a chat transcript.
_DIRECTIVE_NAME = re.compile(r"(--)?[a-z][a-z0-9-]{0,39}")

# OpenVPN reads a profile in chunks of 256 bytes (255 inside an inline block); a longer line is split and its tail read as
# a line of its own. Stay clear of both limits.
_MAX_LINE_BYTES = 250
_MAX_PARMS = 16

# The only options the server may push; a catch-all "ignore" follows them (first match wins, and the profile cannot add
# filters of its own). Default-deny on purpose: OpenVPN matches a filter against the raw text of a pushed option and only
# then strips quotes, so a deny-list is defeated by pushing '"redirect-gateway" def1'. Trailing blanks keep "route " from
# also letting "route-ipv6" or "route-metric" in.
PULL_ACCEPT = (
    "route ",
    "route-gateway ",
    "ifconfig ",
    "topology ",
    "ping ",
    "ping-restart ",
    "peer-id ",
    "cipher ",
    "protocol-flags ",
    "key-derivation ",
    "tun-mtu ",
    "explicit-exit-notify",
    "auth-token",
    # A long PUSH_REPLY arrives in parts; without this marker OpenVPN takes the first part for the whole reply and
    # brings the device up before the part that carries `ifconfig` has arrived.
    "push-continuation ",
)

_PUSH_REPLY = re.compile(r"PUSH: Received control message: 'PUSH_REPLY,(.*)'")

INLINE_TAGS = frozenset({"ca", "cert", "key", "extra-certs", "tls-auth", "tls-crypt", "tls-crypt-v2", "auth-user-pass"})


class Refused(Exception):
    """The request is not going to be carried out; the message is for the user."""


class Interrupted(BaseException):
    """A signal asked `up` to stop; raised so that its clean-up runs before the process ends. Raised by `_checkpoint`,
    never by the signal handler: an exception thrown into whatever happened to be running — the clean-up of an earlier
    signal, a subprocess call half way through — is what left tunnels, timers and profiles behind."""


_pending_signal = 0  # the first signal seen while `up` runs
_interrupted = False  # set once it has been acted on: a caller killed together with its sudo delivers the signal twice


def _interrupt(signum: int, frame: object) -> None:
    global _pending_signal
    if not _pending_signal:
        _pending_signal = signum


def _checkpoint(note: str = "") -> None:
    """Stop here if a signal has come in. Called only between steps of `up`, where what has been started so far is known
    and the clean-up for it is in place; once it has raised, later signals change nothing."""
    global _interrupted
    if _pending_signal and not _interrupted:
        _interrupted = True
        raise Interrupted(f"signal {_pending_signal}{note}")


# --- profile allowlist ------------------------------------------------------------------------------------------


def _no_args(args: list[str]) -> bool:
    return not args


_NUMBER = re.compile(r"[0-9]{1,9}")  # str.isdigit() also says yes to "²", which int() then rejects


def _ints(low: int, high: int, least: int = 1, most: int = 1) -> Callable[[list[str]], bool]:
    def check(args: list[str]) -> bool:
        return least <= len(args) <= most and all(_NUMBER.fullmatch(a) and low <= int(a) <= high for a in args)

    return check


def _one_of(*values: str) -> Callable[[list[str]], bool]:
    return lambda args: len(args) == 1 and args[0] in values


def _algo(args: list[str]) -> bool:
    return len(args) == 1 and bool(_ALGO.fullmatch(args[0]))


def _is_host(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return bool(_HOSTNAME.fullmatch(text))
    return True


_PROTOS = ("udp", "udp4", "udp6", "tcp", "tcp4", "tcp6", "tcp-client", "tcp4-client", "tcp6-client")


def _remote(args: list[str]) -> bool:
    if not 1 <= len(args) <= 3 or not _is_host(args[0]):
        return False
    if len(args) >= 2 and not (_NUMBER.fullmatch(args[1]) and 1 <= int(args[1]) <= 65535):
        return False
    return len(args) < 3 or args[2] in _PROTOS


def _verify_x509_name(args: list[str]) -> bool:
    return 1 <= len(args) <= 2 and all(0 < len(a) <= 200 for a in args) and (len(args) == 1 or args[1] in ("subject", "name", "name-prefix"))


def _tls_version(args: list[str]) -> bool:
    return 1 <= len(args) <= 2 and args[0] in ("1.0", "1.1", "1.2", "1.3") and (len(args) == 1 or args[1] == "or-highest")


def _resolv_retry(args: list[str]) -> bool:
    return len(args) == 1 and (args[0] == "infinite" or bool(_NUMBER.fullmatch(args[0])))


def _anything(args: list[str]) -> bool:
    return True


KEEP, DROP = "keep", "drop"

# directive -> (argument check, keep it or drop it). Dropped ones are accepted in a profile but the backend sets its own
# value on the command line (or, for dhcp-option, deliberately does nothing). Anything not listed refuses the profile.
_DIRECTIVES: dict[str, tuple[Callable[[list[str]], bool], str]] = {
    "client": (_no_args, KEEP),
    "pull": (_no_args, KEEP),
    "tls-client": (_no_args, KEEP),
    "proto": (_one_of(*_PROTOS), KEEP),
    "remote": (_remote, KEEP),
    "remote-random": (_no_args, KEEP),
    "remote-random-hostname": (_no_args, KEEP),
    "nobind": (_no_args, KEEP),
    "float": (_no_args, KEEP),
    "persist-key": (_no_args, KEEP),
    "persist-tun": (_no_args, KEEP),
    "auth-nocache": (_no_args, KEEP),
    "mute-replay-warnings": (_no_args, KEEP),
    "remote-cert-tls": (_one_of("server"), KEEP),
    "verify-x509-name": (_verify_x509_name, KEEP),
    "cipher": (_algo, KEEP),
    "data-ciphers": (_algo, KEEP),
    "data-ciphers-fallback": (_algo, KEEP),
    "auth": (_algo, KEEP),
    "tls-cipher": (_algo, KEEP),
    "tls-ciphersuites": (_algo, KEEP),
    "tls-groups": (_algo, KEEP),
    "tls-version-min": (_tls_version, KEEP),
    "tls-version-max": (_one_of("1.0", "1.1", "1.2", "1.3"), KEEP),
    "key-direction": (_one_of("0", "1"), KEEP),
    "reneg-sec": (_ints(0, 604800, 1, 2), KEEP),
    "keepalive": (_ints(1, 3600, 2, 2), KEEP),
    "ping": (_ints(1, 3600), KEEP),
    "ping-restart": (_ints(0, 3600), KEEP),
    "explicit-exit-notify": (_ints(0, 10, 0, 1), KEEP),
    "mssfix": (_ints(0, 9000, 0, 1), KEEP),
    "tun-mtu": (_ints(576, 9000), KEEP),
    "sndbuf": (_ints(0, 16777216), KEEP),
    "rcvbuf": (_ints(0, 16777216), KEEP),
    "connect-retry": (_ints(1, 300, 1, 2), KEEP),
    # Set by the backend after --config, so the profile's value would lose anyway.
    "dev": (lambda args: len(args) == 1 and bool(re.fullmatch(r"tun\d{0,3}", args[0])), DROP),
    "dev-type": (_one_of("tun"), DROP),
    "verb": (_ints(0, 11), DROP),
    "mute": (_ints(0, 1000), DROP),
    "resolv-retry": (_resolv_retry, DROP),
    "connect-retry-max": (_ints(0, 1000), DROP),
    "connect-timeout": (_ints(0, 3600), DROP),
    "server-poll-timeout": (_ints(0, 3600), DROP),
    "hand-window": (_ints(1, 3600), DROP),
    # Inert in OpenVPN 2.6 without a script; dropped so that stays true whatever reads the profile.
    "dhcp-option": (_anything, DROP),
}


def _tokenize(line: str, line_no: int) -> list[str]:
    """One config line -> its parameters, by the rules of OpenVPN's parse_line(): whitespace separates, double and single
    quotes group, a backslash escapes a backslash, a double quote or whitespace outside single quotes, and ';' or '#'
    where a parameter would start begins a comment. Stricter in one way: what OpenVPN warns about, this refuses."""
    tokens: list[str] = []
    state = "initial"
    backslash = False
    parm: list[str] = []
    for ch in line + "\0":
        out = ""
        done = False
        if not backslash and ch == "\\" and state != "squoted":
            backslash = True
            continue
        space = ch == "\0" or ch.isspace()
        if state == "initial":
            if not space:
                if ch in ";#":
                    break
                if not backslash and ch == '"':
                    state = "quoted"
                elif not backslash and ch == "'":
                    state = "squoted"
                else:
                    out, state = ch, "unquoted"
        elif state == "unquoted":
            if not backslash and space:
                done = True
            else:
                out = ch
        elif state == "quoted":
            if not backslash and ch == '"':
                done = True
            else:
                out = ch
        elif ch == "'":  # squoted
            done = True
        else:
            out = ch
        if backslash and out and not (out in '\\"' or out.isspace()):
            raise Refused(f"profile line {line_no}: a backslash may only escape a backslash, a double quote or a space")
        backslash = False
        if out == "\0":
            raise Refused(f"profile line {line_no}: unterminated quote")
        parm.append(out)
        if done:
            tokens.append("".join(parm))
            parm = []
            state = "initial"
            if len(tokens) > _MAX_PARMS:
                raise Refused(f"profile line {line_no}: more than {_MAX_PARMS} parameters")
    if state in ("quoted", "squoted"):
        raise Refused(f"profile line {line_no}: unterminated quote")
    return tokens


def _serialize(tokens: list[str], line_no: int) -> str:
    out = []
    for token in tokens:
        if _BARE_TOKEN.fullmatch(token):
            out.append(token)
        elif token and all(" " <= ch <= "~" and ch not in '"\\' for ch in token):
            out.append(f'"{token}"')
        else:
            raise Refused(f"profile line {line_no}: a parameter holds characters this backend doesn't pass on (quotes, backslashes, non-ASCII or control characters)")
    return " ".join(out)


def sanitize_profile(text: str) -> tuple[str, list[str]]:
    """Vet ``text`` against the allowlist. Returns the profile to hand to OpenVPN and the names of the directives that were
    accepted but left out. Raises Refused, naming the line and the directive, for anything else."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    dropped: list[str] = []
    seen_remote = False
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        line_no = i
        if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            raise Refused(f"profile line {line_no}: longer than {_MAX_LINE_BYTES} bytes (OpenVPN would split it)")
        if any(ch < " " and ch != "\t" for ch in line) or "\x7f" in line:
            raise Refused(f"profile line {line_no}: control character")
        tokens = _tokenize(line, line_no)
        if not tokens:
            continue
        name = tokens[0]
        tag = _INLINE_TAG.fullmatch(name) if len(tokens) == 1 else None
        if tag:
            if tag.group(1) not in INLINE_TAGS:
                raise Refused(f"profile line {line_no}: inline block <{tag.group(1)}> is not allowed by the OpenVPN 2 backend")
            close = f"</{tag.group(1)}>"
            body: list[str] = []
            while True:
                if i >= len(lines):
                    raise Refused(f"profile line {line_no}: {name} is never closed")
                inner = lines[i]
                i += 1
                if len(inner.encode("utf-8")) > _MAX_LINE_BYTES:
                    raise Refused(f"profile line {i}: longer than {_MAX_LINE_BYTES} bytes (OpenVPN would split it)")
                if any(ch < " " and ch not in "\t\r" for ch in inner) or "\x7f" in inner:
                    raise Refused(f"profile line {i}: control character")
                # OpenVPN ends the block at the first line that starts with the closing tag, whatever follows it.
                if inner.lstrip().startswith(close):
                    break
                if tag.group(1) == "key" and "ENCRYPTED" in inner:
                    # "BEGIN ENCRYPTED PRIVATE KEY" / "Proc-Type: 4,ENCRYPTED": OpenVPN would ask for the passphrase
                    # (under systemd: systemd-ask-password, as root) and sit there; nobody is going to answer.
                    raise Refused(f"profile line {i}: the private key is passphrase-protected; this backend can't answer prompts, so the key has to be unencrypted")
                body.append(inner)
            out += [name, *body, close]
            continue
        if name not in _DIRECTIVES:
            hint = " (drop the leading dashes)" if name.startswith("--") and name[2:] in _DIRECTIVES else ""
            shown = f"directive {name!r}" if _DIRECTIVE_NAME.fullmatch(name) else "a line that doesn't start with a directive (text outside an inline block?)"
            raise Refused(
                f"profile line {line_no}: {shown} is not allowed by the OpenVPN 2 backend{hint}. OpenVPN runs as root here, so "
                f"profiles are limited to: {', '.join(sorted(_DIRECTIVES))}; and the inline blocks {', '.join(f'<{t}>' for t in sorted(INLINE_TAGS))}."
            )
        check, action = _DIRECTIVES[name]
        if not check(tokens[1:]):
            raise Refused(f"profile line {line_no}: {name!r} has parameters the OpenVPN 2 backend doesn't accept")
        if action == DROP:
            if name not in dropped:
                dropped.append(name)
            continue
        seen_remote = seen_remote or name == "remote"
        out.append(_serialize(tokens, line_no))
    if not seen_remote:
        raise Refused("the profile has no 'remote' line, so there is nothing to connect to (is this an .ovpn file?)")
    return "\n".join(out) + "\n", dropped


# --- host I/O (monkeypatched in tests) --------------------------------------------------------------------------


def _run(argv: list[str], *, input_text: str | None = None, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    if signal.getsignal(signal.SIGTERM) is not _interrupt:
        return subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)
    # `up` is running. Its tools share its process group, and a signal meant for `up` (sudo's pty mode and a terminal
    # hang-up signal the whole group, more than once) would kill them too: first the tool a check is waiting for, then
    # the clean-up's own `systemctl stop`. The tool inherits the blocked signals; the handler takes its note when they
    # are let through again. A tool that hangs is still ended by the timeout, which uses SIGKILL.
    signal.pthread_sigmask(signal.SIG_BLOCK, _SIGNALS)
    try:
        return subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, _SIGNALS)


def _json_cmd(argv: list[str]) -> Any:
    proc = _run(argv)
    if proc.returncode != 0:
        raise Refused(f"{' '.join(argv[:4])} failed: {(proc.stderr.strip() or proc.stdout.strip() or proc.returncode)}")
    return json.loads(proc.stdout or "null")


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _now() -> float:
    return time.monotonic()


def _is_root() -> bool:
    return os.geteuid() == 0


# --- naming -----------------------------------------------------------------------------------------------------


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise Refused("the profile name must be 1-64 characters of A-Z a-z 0-9 _ . - and start with a letter or digit")
    return name


def unit_name(name: str) -> str:
    return f"{UNIT_PREFIX}{check_name(name)}.service"


def rollback_unit(name: str, suffix: str = "service") -> str:
    """The rollback's service, or with suffix="timer" its timer. The suffix is always spelled out: a profile named
    `x.timer` would otherwise have its own last label read as the unit type."""
    return f"{ROLLBACK_PREFIX}{check_name(name)}.{suffix}"


def device_name(name: str) -> str:
    # Interface names are at most 15 bytes; the hash keeps two profiles on two devices.
    return DEVICE_PREFIX + hashlib.sha256(name.encode()).hexdigest()[:10]


def profile_path(name: str) -> Path:
    return RUN_DIR / f"{check_name(name)}.conf"


def ok_path(name: str) -> Path:
    """Written once a tunnel has passed its checks, removed with it. World-readable: status needs no root to see it."""
    return RUN_DIR / f"{check_name(name)}.ok"


# --- openvpn and systemd ----------------------------------------------------------------------------------------


def openvpn_version() -> tuple[tuple[int, ...] | None, str]:
    """((major, minor, patch), first line of --version), or (None, why) when openvpn can't be run."""
    try:
        proc = _run([OPENVPN, "--version"])
    except OSError as exc:
        return None, f"{OPENVPN}: {exc.strerror or exc}"
    first = (proc.stdout.splitlines() or [""])[0]
    match = re.match(r"OpenVPN (\d+)\.(\d+)(?:\.(\d+))?", first)
    if not match:
        return None, first or proc.stderr.strip() or f"{OPENVPN} --version printed nothing"
    return tuple(int(g) for g in match.groups(default="0")), first


def check_openvpn() -> str:
    version, text = openvpn_version()
    if version is None:
        raise Refused(f"OpenVPN 2 isn't usable here ({text}). Install the distribution's openvpn package.")
    if version[:2] not in SUPPORTED_VERSIONS:
        supported = ", ".join(f"{a}.{b}.x" for a, b in SUPPORTED_VERSIONS)
        raise Refused(f"OpenVPN {'.'.join(map(str, version))} is not a version this backend supports ({supported}).")
    return text


def show_unit(unit: str) -> dict[str, str]:
    """Selected properties of ``unit``. A unit that doesn't exist reads as LoadState=not-found; systemctl exits 0 for it."""
    proc = _run(["systemctl", "show", unit, "--no-pager", "-p", "LoadState,ActiveState,SubState,StatusText,Result,ExecMainStatus,InvocationID"])
    if proc.returncode != 0:
        raise Refused(f"systemctl show {unit} failed: {proc.stderr.strip() or proc.returncode}")
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)


def unit_state(props: dict[str, str]) -> str:
    """'absent', 'connected', 'connecting', 'stopping' or 'failed' — what the unit's properties say about the tunnel.
    systemd calls the unit active as soon as OpenVPN has parsed its options, before any connection attempt; the tunnel
    is up only once OpenVPN has reported CONNECTED_STATUS."""
    if props.get("LoadState") in (None, "", "not-found"):
        return "absent"
    active = props.get("ActiveState", "")
    if active in ("activating", "reloading"):
        return "connecting"
    if active == "active":
        return "connected" if props.get("StatusText") == CONNECTED_STATUS else "connecting"
    if active == "deactivating":
        return "stopping"
    if active == "inactive" and props.get("Result", "success") == "success":
        return "absent"
    return "failed"


def describe(name: str, props: dict[str, str]) -> dict[str, str]:
    state = unit_state(props)
    detail = props.get("StatusText") or props.get("SubState") or ""
    if state == "failed":
        detail = f"{props.get('Result', '?')}, exit status {props.get('ExecMainStatus', '?')}"
    elif state == "connected" and not ok_path(name).exists():
        # OpenVPN is through, but `up` hasn't finished checking the tunnel (or died doing so; the rollback timer is
        # still armed then). Not something to report as usable, or to adopt as already connected.
        state, detail = "connecting", "tunnel established, checks not finished"
    return {"name": name, "unit": unit_name(name), "state": state, "detail": detail}


def list_units() -> list[str]:
    """Profile names of every unit this backend started that systemd still knows."""
    proc = _run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager", "--type=service", f"{UNIT_PREFIX}*"])
    if proc.returncode != 0:
        raise Refused(f"systemctl list-units failed: {proc.stderr.strip() or proc.returncode}")
    names = []
    for line in proc.stdout.splitlines():
        unit = (line.split() or [""])[0]
        if unit.startswith(UNIT_PREFIX) and unit.endswith(".service"):
            names.append(unit[len(UNIT_PREFIX) : -len(".service")])
    return names


# Journal lines that say nothing about why a connect failed.
_LOG_NOISE = ("WARNING: you are using user/group", "Started openvpn3-on-demand", "Deactivated successfully", "library versions:", "Socket Buffers:", "UDPv4 link local")


def journal_lines(unit: str, lines: int = 40, *, invocation: str = "", since: float = 0.0) -> list[str]:
    """OpenVPN's last log lines (``lines`` 0: all of them). With ``invocation`` (the unit's InvocationID) only that run's:
    a BYO profile, or an ephemeral one reconnecting within a task, reuses its unit name, and an earlier run's PUSH_REPLY
    or failure is not this one's. Where no InvocationID is known (the unit never got going), ``since`` (epoch seconds)
    bounds it instead."""
    match = [f"_SYSTEMD_INVOCATION_ID={invocation}"] if invocation else ["-u", unit, *(["--since", f"@{int(since)}"] if since else [])]
    try:
        proc = _run(["journalctl", *match, *(["-n", str(lines)] if lines else []), "-o", "cat", "--no-pager", "-q"])
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip() and not any(noise in line for noise in _LOG_NOISE)]


def diagnose(log: list[str]) -> str:
    """One sentence on why a connect failed, from OpenVPN's own log lines; empty when nothing recognisable is there."""
    text = "\n".join(log)
    if "Options error" in text:
        return "OpenVPN rejected an option; see the log lines."
    if "Cannot resolve host address" in text:
        return "The VPN endpoint's name didn't resolve. An on-demand endpoint (an AWS Client VPN with no subnet associated) has no address until it is brought up."
    if "VERIFY ERROR" in text or "VERIFY X509NAME ERROR" in text or "VERIFY KU ERROR" in text or "VERIFY EKU ERROR" in text:
        return "The server's certificate was not accepted: the profile's <ca> or verify-x509-name doesn't match this endpoint."
    if "AUTH_FAILED" in text:
        return "The server refused the credentials."
    if "TLS key negotiation failed" in text:
        if "VERIFY OK: depth=0" in text:
            return "The server answered and its certificate verified, but the handshake never finished: the client certificate was rejected or expired, or traffic is filtered."
        return "The TLS handshake never finished: the endpoint stopped answering or the traffic is filtered."
    if "Server poll timeout" in text or "ECONNREFUSED" in text:
        return "The VPN endpoint didn't answer. Is it up (an on-demand endpoint has to be brought up first), and is its port reachable from here?"
    if "Cannot ioctl TUNSETIFF" in text or "Cannot open TUN/TAP" in text:
        return "OpenVPN couldn't create its tunnel device."
    return ""


def openvpn_argv(name: str, config: Path, *, drop_privileges: bool) -> list[str]:
    """The openvpn command line. Order matters: pull filters before --config (first match wins, and the profile can't
    add any), the backend's own settings after it (for single-valued options the last one wins)."""
    argv = [OPENVPN]
    for prefix in PULL_ACCEPT:
        argv += ["--pull-filter", "accept", prefix]
    argv += ["--pull-filter", "ignore", ""]  # the empty prefix matches every option the accepts above did not
    argv += ["--config", str(config)]
    argv += ["--dev", device_name(name), "--dev-type", "tun"]
    # OpenVPN installs no routes at all — not the pushed ones, not a redirect-gateway: the helper adds the vetted ones.
    argv += ["--route-noexec"]
    # --verb 3 and --mute 0 are what make OpenVPN log every part of the server's PUSH_REPLY, which the helper reads.
    argv += ["--script-security", "1", "--suppress-timestamps", "--verb", "3", "--mute", "0", "--auth-nocache", "--persist-tun", "--persist-key"]
    # Every phase of a connect is bounded, and a tunnel that loses its server exits instead of reconnecting on its own:
    # with --remap-usr1 every internal restart (ping timeout, TLS error, server gone) becomes an exit. That also means
    # one address per attempt, and always the first: a backup `remote` is only ever reached with `remote-random`.
    argv += ["--resolv-retry", "15", "--connect-timeout", "20", "--hand-window", "30", "--remap-usr1", "SIGTERM"]
    argv += ["--inactive", str(INACTIVE_SECS), str(INACTIVE_BYTES)]
    if drop_privileges:
        argv += ["--user", "openvpn", "--group", "openvpn"]
    return argv


def systemd_run_argv(name: str, config: Path, *, drop_privileges: bool) -> list[str]:
    """A transient unit shaped like the distribution's openvpn-client@.service, plus the lifetime limit and clean-up."""
    properties = [
        "Type=notify",
        "PrivateTmp=true",
        "ProtectSystem=true",
        "ProtectHome=true",
        "NoNewPrivileges=true",
        "CapabilityBoundingSet=CAP_IPC_LOCK CAP_NET_ADMIN CAP_NET_RAW CAP_SETGID CAP_SETUID CAP_SETPCAP CAP_SYS_CHROOT CAP_DAC_OVERRIDE",
        "LimitNPROC=10",
        "DeviceAllow=/dev/null rw",
        "DeviceAllow=/dev/net/tun rw",
        "Restart=no",
        # OpenVPN reports ready as soon as its options are parsed; a start that takes longer than this is stuck.
        "TimeoutStartSec=30",
        "TimeoutStopSec=20",
        f"RuntimeMaxSec={MAX_LIFETIME_SECS}",
        # The profile holds the private key: it goes when the tunnel goes, however that happens. So does the marker
        # that says the tunnel passed its checks.
        f"ExecStopPost=/usr/bin/rm -f {config} {ok_path(name)}",
    ]
    argv = ["systemd-run", "--quiet", "--collect", f"--unit={unit_name(name)}", f"--description=openvpn3-on-demand tunnel {name}"]
    for prop in properties:
        argv += ["-p", prop]
    return argv + openvpn_argv(name, config, drop_privileges=drop_privileges)


def _user_exists(user: str) -> bool:
    try:
        import pwd

        pwd.getpwnam(user)
    except (KeyError, ImportError):
        return False
    return True


# --- routes -----------------------------------------------------------------------------------------------------


def parse_nameservers(text: str) -> list[str]:
    servers = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            servers.append(fields[1])
    return servers


def protected_addresses() -> list[str]:
    """IPv4 addresses this host must keep reaching outside the tunnel: its default gateways, its DNS servers and the
    cloud metadata address. Collected before the tunnel exists."""
    found = [METADATA_ADDRESS]
    for route in _json_cmd(["ip", "-j", "-4", "route", "show", "default"]) or []:
        if route.get("gateway"):
            found.append(route["gateway"])
    for path in RESOLV_CONFS:
        try:
            found += parse_nameservers(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    keep = []
    for addr in found:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.version == 4 and not ip.is_loopback and addr not in keep:
            keep.append(addr)
    return keep


def host_networks() -> list[ipaddress.IPv4Network]:
    """The IPv4 networks this host is directly attached to (the subnets of its own interfaces), collected before the
    tunnel exists. Addresses in them are the host's neighbours: no tunnel gets to carve a piece out of one. Devices of
    other on-demand tunnels are not the host's own, and a /32 has no neighbours."""
    nets: list[ipaddress.IPv4Network] = []
    for link in _json_cmd(["ip", "-j", "-4", "addr", "show"]) or []:
        if str(link.get("ifname", "")).startswith(DEVICE_PREFIX) or "LOOPBACK" in (link.get("flags") or []):
            continue
        for info in link.get("addr_info", []):
            try:
                net = ipaddress.IPv4Network(f"{info.get('local')}/{info.get('prefixlen')}", strict=False)
            except ValueError:
                continue
            if net.prefixlen < 32 and net not in nets:
                nets.append(net)
    return nets


def _is_private(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    return any(net.subnet_of(private) for private in PRIVATE_NETWORKS if net.version == private.version)


def pushed_routes(log: list[str]) -> tuple[list[str], list[str], bool]:
    """What the server asked to have routed through the tunnel, from OpenVPN's own log of its PUSH_REPLY: (private IPv4
    prefixes to install, the others with the reason they are left out, whether the whole reply has been seen). A long
    reply comes in parts, each but the last marked `push-continuation 2`.

    Of a pushed `route` only network and netmask are used, and only when it asks for the tunnel: no gateway, `vpn_gateway`
    or the pushed route-gateway. Any other gateway (`net_gateway`, an address) is the server asking for that network to
    go somewhere else, which is this host's routing table to decide, not the server's; the metric is ignored — the helper
    binds every route to the tunnel device at its own."""
    matches = [_PUSH_REPLY.fullmatch(line) for line in log]
    replies = [match.group(1).split(",") for match in matches if match]
    complete = bool(replies) and "push-continuation 2" not in [option.strip() for option in replies[-1]]
    options = [option.split() for reply in replies for option in reply]
    tunnel_gateways = {"vpn_gateway"} | {fields[1] for fields in options if len(fields) == 2 and fields[0] == "route-gateway"}
    wanted: list[str] = []
    skipped: list[str] = []
    for fields in options:
        if not fields or fields[0] != "route":
            continue
        text = " ".join(fields[1:3]) or "(nothing)"
        try:
            net = ipaddress.IPv4Network(f"{fields[1]}/{fields[2] if len(fields) > 2 else '255.255.255.255'}")
        except (ValueError, IndexError):
            skipped.append(f"{text}: not a network and netmask")
            continue
        if len(fields) > 3 and fields[3] not in tunnel_gateways:
            skipped.append(f"{net}: the VPN asked for it to go through {fields[3]}, not through the tunnel")
        elif not _is_private(net):
            skipped.append(f"{net}: outside the private ranges")
        elif str(net) not in wanted:
            wanted.append(str(net))
    return wanted, skipped, complete


def route_to(addr: str) -> tuple[str, str, str] | None:
    """How the kernel would send a packet to ``addr`` right now: (type, gateway, device), or None for "no route"."""
    lookup = _run(["ip", "-j", "route", "get", addr])
    if lookup.returncode != 0:
        return None
    got = (json.loads(lookup.stdout or "null") or [{}])[0]
    return str(got.get("type", "unicast")), str(got.get("gateway", "")), str(got.get("dev", ""))


def close_ipv6(device: str) -> str:
    """Switch IPv6 off on the tunnel device; returns what went wrong, or "". Nothing IPv6 is pushed (the pull filter sees
    to that), but a tun device still listens for router advertisements, and one sent down the tunnel at any later moment
    would give this host an IPv6 address and default route through the VPN. Switching it off also flushes what such an
    advertisement already put there. The switch goes away with the device."""
    if not IPV6_CONF.is_dir():
        return ""  # this host has no IPv6 at all
    knob = IPV6_CONF / device / "disable_ipv6"
    try:
        if knob.read_text(encoding="ascii").strip() != "1":
            knob.write_text("1\n", encoding="ascii")
            if knob.read_text(encoding="ascii").strip() != "1":
                return f"IPv6 stayed on for {device} after it was switched off"
    except (OSError, ValueError) as exc:
        return f"IPv6 could not be switched off on {device} ({getattr(exc, 'strerror', None) or exc})"
    return ""


def demote_connected_routes(device: str) -> None:
    """Move the route the kernel made for the tunnel's own address to ROUTE_METRIC. It is created at metric 0; where the
    VPN's address pool is the very network this host lives in, that outranks the host's own route to its neighbours
    (Amazon Linux gives it 512). Whether it worked is `tunnel_violations`' business: it reads the metrics back."""
    for link in _json_cmd(["ip", "-j", "-4", "addr", "show", "dev", device]) or []:
        for info in link.get("addr_info", []):
            local, peer, length = info.get("local"), info.get("address"), info.get("prefixlen")
            spec = [str(local), "peer", f"{peer}/{length}"] if peer else [f"{local}/{length}"]
            # `change` on an address that is already there only updates it; the metric is what re-keys its route.
            _run(["ip", "addr", "change", *spec, "dev", device, "metric", str(ROUTE_METRIC)])


def install_routes(device: str, wanted: list[str], baseline: dict[str, tuple[str, str, str] | None], local_nets: Sequence[ipaddress.IPv4Network] = ()) -> list[str]:
    """Add each prefix on the tunnel device; returns the ones left out, with the reason. A prefix inside a network this
    host is attached to is not added, and one that changes how a protected address is reached is taken out again at once."""
    skipped = []
    for prefix in wanted:
        net = ipaddress.IPv4Network(prefix)
        own = next((str(local) for local in local_nets if net.subnet_of(local)), "")
        if own:
            skipped.append(f"{prefix}: inside {own}, a network this host is directly attached to; those addresses stay local")
            continue
        # append, not add: a second tunnel to the same VPN pushes the same prefix, and `add` would refuse it ("File
        # exists") — leaving that tunnel without the route once the first one closes.
        added = _run(["ip", "route", "append", prefix, "dev", device, "metric", str(ROUTE_METRIC)])
        if added.returncode != 0:
            skipped.append(f"{prefix}: {added.stderr.strip() or 'ip route append failed'}")
            continue
        for addr, before in baseline.items():
            if ipaddress.ip_address(addr) in net and route_to(addr) != before:
                _run(["ip", "route", "del", prefix, "dev", device, "metric", str(ROUTE_METRIC)])
                skipped.append(f"{prefix}: it would take over {addr} (this host's gateway, DNS server or metadata address)")
                break
    return skipped


def shadowed_routes(device: str, prefixes: list[str]) -> list[str]:
    """Of the tunnel's routes, the ones the kernel does not use for now: an older route for the very same prefix (another
    tunnel's, or one of the host's own) comes first and keeps the traffic. Nothing is wrong with that — but a tunnel
    reported as carrying a prefix whose packets leave elsewhere is a riddle for whoever uses it, so it is said."""
    notes = []
    for prefix in prefixes:
        listed = _json_cmd(["ip", "-j", "-4", "route", "show", "exact", prefix]) or []
        first = str(listed[0].get("dev", "")) if listed else device
        if first != device:
            notes.append(f"{prefix}: {first} already has a route for it and keeps that traffic until its route is gone")
    return notes


def tunnel_violations(device: str, baseline: dict[str, tuple[str, str, str] | None], local_nets: Sequence[ipaddress.IPv4Network] = ()) -> tuple[list[str], list[str]]:
    """(what is wrong with the tunnel as it stands, the IPv4 routes on its device). The address the server handed out is
    the one thing it still controls here, so it is checked: private, not one of the protected addresses, and no route on
    the device that is outside the private ranges, could outrank the host's own routes, or takes a piece out of a
    network the host is attached to. Then every protected address must be reached exactly as before."""
    problems: list[str] = []
    routes: list[str] = []
    has_ipv4 = False
    for link in _json_cmd(["ip", "-j", "addr", "show", "dev", device]) or []:
        for info in link.get("addr_info", []):
            local = str(info.get("local", ""))
            try:
                ip = ipaddress.ip_address(local)
            except ValueError:
                problems.append(f"unreadable address {local!r} on {device}")
                continue
            if ip.version == 6:
                if not ip.is_link_local:
                    problems.append(f"IPv6 address {ip} on the tunnel (IPv6 over the tunnel is not supported by this backend)")
            elif not _is_private(ipaddress.ip_network(f"{ip}/32")):
                problems.append(f"the VPN gave the tunnel the address {ip}, which is not a private address")
            elif local in baseline:
                problems.append(f"the VPN gave the tunnel the address {ip}, which is this host's gateway, DNS server or metadata address")
            else:
                has_ipv4 = True
    if not has_ipv4 and not problems:
        # OpenVPN reported the tunnel up, yet the device carries nothing: not something to call connected.
        problems.append(f"the tunnel device {device} has no IPv4 address (the VPN's reply did not carry a usable one)")
    for route in _json_cmd(["ip", "-j", "-4", "route", "show", "dev", device]) or []:
        dst = str(route.get("dst", ""))
        try:
            net = ipaddress.ip_network("0.0.0.0/0" if dst == "default" else dst, strict=False)
        except ValueError:
            problems.append(f"unreadable route {dst!r} on {device}")
            continue
        routes.append(str(net))
        if not _is_private(net):
            problems.append(f"route {net} is not inside a private range ({', '.join(str(p) for p in PRIVATE_NETWORKS)})")
        try:
            metric = int(route.get("metric", 0))
        except (TypeError, ValueError):
            metric = 0
        if metric < ROUTE_METRIC:
            problems.append(f"route {net} on the tunnel has metric {metric}, below {ROUTE_METRIC}, so it could outrank this host's own route to that network")
        own = next((str(local) for local in local_nets if net != local and net.subnet_of(local)), "")
        if own:
            problems.append(f"the VPN's own network {net} lies inside {own}, a network this host is directly attached to, and would take those addresses over")
    for route in _json_cmd(["ip", "-j", "-6", "route", "show", "dev", device]) or []:
        dst = str(route.get("dst", ""))
        try:
            net6 = ipaddress.ip_network("::/0" if dst == "default" else dst, strict=False)
        except ValueError:
            problems.append(f"unreadable IPv6 route {dst!r} on {device}")
            continue
        if not (net6.is_link_local or net6.is_multicast):
            problems.append(f"IPv6 route {net6} (IPv6 over the tunnel is not supported by this backend)")
    for addr, before in baseline.items():
        now = route_to(addr)
        if now != before:
            problems.append(f"{addr} (this host's gateway, DNS server or metadata address) is reached differently with the tunnel up: {_how(before)} before, {_how(now)} now")
    return problems, routes


def _how(route: tuple[str, str, str] | None) -> str:
    if route is None:
        return "no route"
    kind, gateway, dev = route
    if kind == "local":
        return "delivered to this host itself"
    return (f"via {gateway} " if gateway else "directly ") + f"on {dev}"


# --- commands ---------------------------------------------------------------------------------------------------


def _stop(unit: str) -> None:
    _run(["systemctl", "stop", unit], timeout=60.0)
    # A unit that failed to start can linger as "failed" when --collect didn't get to it.
    _run(["systemctl", "reset-failed", unit])


def _arm_rollback(name: str) -> None:
    """A transient timer that stops the tunnel ROLLBACK_SECS from now. systemd owns it, so it fires whatever happens to
    this process; `up` cancels it once the tunnel has passed its checks."""
    argv = ["systemd-run", "--quiet", "--collect", f"--unit={rollback_unit(name)}", f"--on-active={ROLLBACK_SECS}", "--timer-property=AccuracySec=1s"]
    argv += [f"--description=openvpn3-on-demand rollback for {name}", "/usr/bin/systemctl", "stop", unit_name(name)]
    proc = _run(argv)
    if proc.returncode != 0:
        # Most likely a timer left under this name by a connect that was killed outright. The caller holds the lock and
        # found no unit, so nothing depends on it: clear it and try once more.
        _disarm_rollback(name)
        _run(["systemctl", "reset-failed", rollback_unit(name), rollback_unit(name, "timer")])
        proc = _run(argv)
    if proc.returncode != 0:
        raise Refused(f"couldn't arm the rollback timer, so the tunnel was not started: {proc.stderr.strip() or proc.returncode}")


def _rollback_armed(name: str) -> bool:
    return _run(["systemctl", "is-active", "--quiet", rollback_unit(name, "timer")]).returncode == 0


def _disarm_rollback(name: str) -> bool:
    """Cancel the rollback timer; True only once systemd itself has said it is no longer armed."""
    timer = rollback_unit(name, "timer")
    _run(["systemctl", "stop", timer])
    # 3 is is-active's own "not active" (4: a unit it does not know). A systemctl that was killed or could not ask has
    # said nothing about the timer, and a tunnel reported connected with its timer still armed is stopped under its user.
    return _run(["systemctl", "is-active", "--quiet", timer]).returncode in (3, 4)


def _fail_started(name: str, message: str, *, with_log: bool = True, invocation: str = "", since: float = 0.0) -> dict[str, Any]:
    """Stop the unit this call started, clean up, and build the error. `with_log` adds what OpenVPN said last."""
    unit = unit_name(name)
    log = journal_lines(unit, invocation=invocation, since=since) if with_log else []
    try:
        _stop(unit)
    except (OSError, subprocess.TimeoutExpired):
        pass  # one bounded attempt; whether the unit is gone is asked below, and the rest of the clean-up still runs
    try:
        gone = unit_state(show_unit(unit)) == "absent"
    except (Refused, OSError, subprocess.TimeoutExpired):
        gone = False
    if gone:
        _disarm_rollback(name)
    else:
        # A timer is what still stands between this host and a tunnel nobody vouches for: it stays armed, and where the
        # checks had already cancelled it, a new one is set — the sentence below must not promise a timer that isn't there.
        try:
            armed = _rollback_armed(name)
            if not armed:
                _arm_rollback(name)
                armed = True
        except (Refused, OSError, subprocess.TimeoutExpired):
            armed = False
        if armed:
            message += f" The unit could not be confirmed stopped; a timer stops it within {ROLLBACK_SECS // 60} minutes."
        else:
            message += f" The unit could not be confirmed stopped and no timer could be set to stop it: run `sudo systemctl stop {unit}`."
    profile_path(name).unlink(missing_ok=True)
    ok_path(name).unlink(missing_ok=True)
    if log:
        hint = diagnose(log)
        message += (f" {hint}" if hint else "") + " Last log lines: " + " | ".join(log[-6:])
    return {"status": "error", "name": name, "message": message}


def _confirm(name: str, baseline: dict[str, tuple[str, str, str] | None], local_nets: list[ipaddress.IPv4Network], dropped: list[str], since: float) -> dict[str, Any]:
    """Everything between "the unit was started" and "the tunnel is usable": wait for OpenVPN, add the routes, check."""
    unit = unit_name(name)
    device = device_name(name)
    deadline = _now() + CONNECT_TIMEOUT_SECS
    invocation = ""  # this run of the unit, for reading its log lines and nobody else's
    ipv6_closed = False
    ipv6_problem = ""
    while True:
        _checkpoint()
        props = show_unit(unit)
        invocation = props.get("InvocationID") or invocation
        state = unit_state(props)
        if state == "connected":
            break
        if state in ("absent", "failed", "stopping"):
            return _fail_started(name, "Connect failed: OpenVPN exited before the tunnel came up.", invocation=invocation, since=since)
        if _now() >= deadline:
            return _fail_started(name, f"Connect did not finish within {CONNECT_TIMEOUT_SECS:g}s ({props.get('StatusText') or 'no status'}).", invocation=invocation, since=since)
        # The device exists a moment before OpenVPN reports the tunnel up, and from then on it takes router
        # advertisements: look for it more often than systemd is asked, and close IPv6 on it the moment it is there.
        for _ in range(4):
            if not ipv6_closed and (IPV6_CONF / device).is_dir():
                ipv6_problem, ipv6_closed = close_ipv6(device), True
            _sleep(0.05)
    if not invocation:
        return _fail_started(name, "The tunnel came up, but systemd gave no InvocationID for its unit, so its log could not be told from an earlier run's.", with_log=False)
    if not ipv6_closed:
        ipv6_problem = close_ipv6(device)
    if ipv6_problem:
        return _fail_started(
            name, f"The tunnel came up and was taken down again: {ipv6_problem}, and a tunnel that still takes IPv6 router advertisements is not left up.", with_log=False
        )

    # What the server pushed is in OpenVPN's log before it reports the tunnel up; allow the journal a moment to have it.
    # All of this run's lines are read: a reply with hundreds of filtered options logs a line for each, and a window of
    # the last few hundred would have lost its first parts — and with them routes — without a trace.
    journal_deadline = _now() + 5.0
    while True:
        _checkpoint()
        wanted, skipped, complete = pushed_routes(journal_lines(unit, 0, invocation=invocation))
        if complete or _now() >= journal_deadline:
            break
        _sleep(0.2)
    if not complete:
        return _fail_started(
            name,
            "The tunnel came up, but what the VPN pushed could not be read in full from the journal (`journalctl -u " + unit + "`), so its "
            "routes could not be vetted and it was taken down again. Is journald storing this unit's output?",
            with_log=False,
        )
    _checkpoint()
    demote_connected_routes(device)
    skipped += install_routes(device, wanted, baseline, local_nets)
    problems, routes = tunnel_violations(device, baseline, local_nets)
    if problems:
        return _fail_started(
            name,
            "The tunnel came up and was taken down again, because it would have cut this host off or carried traffic that isn't private: " + "; ".join(problems) + ".",
            with_log=False,
        )
    shadowed = shadowed_routes(device, routes)
    # The last point at which a signal still takes the tunnel down. From here on it is a checked tunnel, and is reported
    # as one: a caller that hung up finds it again under its name.
    _checkpoint()
    # Marker first, then the timer: dying between the two leaves a checked tunnel that the timer still removes, never one
    # that is up, unmarked and with nothing left to clear it.
    marker = ok_path(name)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"routes": routes, "skipped_routes": skipped, "shadowed_routes": shadowed}, handle)
    os.chmod(marker, 0o644)
    if not _disarm_rollback(name):
        return _fail_started(name, "The rollback timer could not be cancelled, so the tunnel would have been stopped under you; it was taken down instead.", with_log=False)
    # The timer may have fired, or OpenVPN gone, while the checks ran: say so instead of reporting a tunnel that isn't there.
    if unit_state(show_unit(unit)) != "connected":
        return _fail_started(name, "The tunnel went away while it was being checked.", invocation=invocation)
    return {
        "status": "connected",
        "name": name,
        "unit": unit,
        "device": device,
        "routes": routes,
        "skipped_routes": skipped,
        "shadowed_routes": shadowed,
        "protected": list(baseline),
        "dropped_directives": dropped,
    }


class _Lock:
    """One up or down at a time. Two connects racing for one name would each delete what the other just wrote, and two
    tunnels adding routes at once would each check a routing table the other is changing."""

    def __init__(self, wait: float = 90.0) -> None:
        self.wait = wait
        self.fd = -1

    def __enter__(self) -> _Lock:
        RUN_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chmod(RUN_DIR, 0o755)
        self.fd = os.open(RUN_DIR / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        deadline = _now() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if _now() >= deadline or (_pending_signal and not _interrupted):
                    os.close(self.fd)
                    _checkpoint("; no tunnel was started")  # a caller that hung up while waiting just leaves
                    raise Refused(f"another connect or disconnect has been running for {self.wait:g}s; try again when it has finished") from None
                _sleep(0.2)

    def __exit__(self, *exc: object) -> None:
        os.close(self.fd)  # closing releases the lock


_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def cmd_up(name: str, *, byo: bool, profile_text: str | None) -> dict[str, Any]:
    if not _is_root():
        raise Refused("'up' has to run as root (the MCP server calls it through sudo -n)")
    unit = unit_name(name)
    if byo:
        source = BYO_DIR / f"{name}.conf"
        try:
            profile_text = source.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"status": "no_config", "name": name, "message": f"No OpenVPN 2 profile at {source}."}
        except (OSError, UnicodeDecodeError) as exc:
            raise Refused(f"cannot read {source}: {exc}") from exc
    if profile_text is None:
        raise Refused("no profile given")
    check_openvpn()
    sanitized, dropped = sanitize_profile(profile_text)
    with _Lock():
        return _up_locked(name, unit, sanitized, dropped)


def _up_locked(name: str, unit: str, sanitized: str, dropped: list[str]) -> dict[str, Any]:
    props = show_unit(unit)
    if unit_state(props) != "absent":
        # Not an error of this call: someone's tunnel under this name is there (or on its way). The caller decides.
        state = describe(name, props)["state"]
        return {"status": "exists", "name": name, "state": state, "message": f"unit {unit} already exists ({state}); stop it with `sudo systemctl stop {unit}`, then retry"}
    # How this host reaches what it must not lose, and which networks it sits in, before any tunnel exists.
    baseline = {addr: route_to(addr) for addr in protected_addresses()}
    local_nets = host_networks()
    since = time.time() - 1  # what this unit name logged before now belongs to earlier tunnels
    config = profile_path(name)
    try:
        _checkpoint("; no tunnel was started")
        ok_path(name).unlink(missing_ok=True)
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(sanitized)
        restorecon = shutil.which("restorecon")
        if restorecon:  # SELinux hosts: give the profile the label its location calls for; a no-op elsewhere
            _run([restorecon, "-F", str(config)])
        _arm_rollback(name)
        _checkpoint("; no tunnel was started")
    except BaseException:
        # Nothing was started, so nothing will run the unit's clean-up: the key material goes now, and so does the timer
        # (left armed, it would refuse the next connect under this name until it fired).
        config.unlink(missing_ok=True)
        _disarm_rollback(name)
        raise
    try:
        # No checkpoint between the timer and the unit: a signal that came in while systemd-run was at work is acted on
        # once it has returned and what it started is known.
        drop = _user_exists("openvpn")
        started = _run(systemd_run_argv(name, config, drop_privileges=drop), timeout=60.0)
        if started.returncode != 0:
            return _fail_started(name, f"OpenVPN didn't start: {started.stderr.strip() or 'systemd-run exited ' + str(started.returncode)}.", since=since)
        result = _confirm(name, baseline, local_nets, dropped, since)
        if result["status"] == "connected":
            result["runs_as"] = "openvpn" if drop else "root"  # Debian's package creates no account to drop to
        return result
    except BaseException as exc:
        # Whatever stopped the checks — a tool failing, a signal, a hang-up — an unchecked tunnel is not left up for the
        # rollback timer to find two minutes later. Nothing can cut this clean-up short: the signal handler only takes
        # a note, and the note has been acted on.
        result = _fail_started(name, f"The tunnel was started but could not be checked ({type(exc).__name__}: {exc}), so it was taken down again.", with_log=False)
        if isinstance(exc, (Refused, OSError, ValueError, subprocess.TimeoutExpired)):
            return result
        if isinstance(exc, Interrupted):
            raise Interrupted(result["message"]) from None  # what really happened to the tunnel, for whoever reports it
        raise


def cmd_down(name: str) -> dict[str, Any]:
    if not _is_root():
        raise Refused("'down' has to run as root (the MCP server calls it through sudo -n)")
    with _Lock():
        return _down_locked(name)


def _down_locked(name: str) -> dict[str, Any]:
    unit = unit_name(name)
    before = unit_state(show_unit(unit))
    if before == "absent":
        # Still a full reset for the name: a connect that was killed outright can have left its profile or its timer.
        _disarm_rollback(name)
        profile_path(name).unlink(missing_ok=True)
        ok_path(name).unlink(missing_ok=True)
        return {"status": "not_connected", "name": name}
    stopped = _run(["systemctl", "stop", unit], timeout=60.0)
    _run(["systemctl", "reset-failed", unit])
    _disarm_rollback(name)
    profile_path(name).unlink(missing_ok=True)
    ok_path(name).unlink(missing_ok=True)
    after = unit_state(show_unit(unit))
    if stopped.returncode != 0 and after != "absent":
        return {"status": "error", "name": name, "message": f"systemctl stop {unit} failed: {stopped.stderr.strip() or stopped.returncode}"}
    return {"status": "disconnected", "name": name, "cleared": after == "absent"}


def cmd_status(name: str | None) -> dict[str, Any]:
    # A unit under our prefix whose name this backend could not have produced isn't one of its tunnels.
    names = [check_name(name)] if name else [n for n in list_units() if _NAME.fullmatch(n)]
    sessions = [describe(n, show_unit(unit_name(n))) for n in names]
    return {"status": "ok", "sessions": [s for s in sessions if s["state"] != "absent"]}


def helper_state() -> str:
    """'bundled' (no installed copy), 'installed', or 'stale': an installed copy that is not this file. A root-owned copy
    is what a restricted sudoers rule runs, so one left over from an earlier plugin version would keep vetting profiles
    and routes by that version's rules without anyone noticing."""
    if not INSTALLED_PATH.is_file():
        return "bundled"
    try:
        same = INSTALLED_PATH.read_bytes() == Path(__file__).resolve().read_bytes()
    except OSError:
        return "stale"
    return "installed" if same else "stale"


def reinstall_hint() -> str:
    return f"sudo install -D -o root -g root -m 0755 {Path(__file__).resolve()} {INSTALLED_PATH}"


def root_argv() -> list[str]:
    """How the unprivileged side runs this helper as root: the root-owned installed copy when there is one (all a
    restricted sudoers rule allows), else this very file under the system python3, which takes sudo rights for python3."""
    if INSTALLED_PATH.is_file():
        return ["sudo", "-n", str(INSTALLED_PATH)]
    return ["sudo", "-n", "/usr/bin/python3", "-I", str(Path(__file__).resolve())]


def sudo_allowed() -> bool | None:
    """Whether sudo would run the helper without asking for a password. `sudo -l <command>` only looks the command up.
    None where that can't be asked: under the kernel's no-new-privileges flag (a command sandbox sets it on the shell,
    not on the MCP server) no setuid program gains root, so sudo fails whatever its policy says."""
    try:
        if re.search(r"^NoNewPrivs:\s*1$", PROC_STATUS.read_text(encoding="ascii", errors="replace"), re.M):
            return None
    except OSError:
        pass
    try:
        return _run(["sudo", "-n", "-l", "--", *root_argv()[2:]]).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def cmd_probe() -> dict[str, Any]:
    version, text = openvpn_version()
    return {
        "status": "ok",
        "protocol": PROTOCOL,
        "openvpn": text,
        "supported": version is not None and version[:2] in SUPPORTED_VERSIONS,
        "systemd_run": shutil.which("systemd-run") is not None,
        "sudo": sudo_allowed(),
        "helper": helper_state(),
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, json.dumps({"status": "error", "message": f"usage: {message}"}) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="ovpn2_helper.py", description="Root-side helper of the openvpn3-on-demand OpenVPN 2 backend.")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="start a tunnel; the profile comes from stdin unless --byo")
    up.add_argument("name")
    up.add_argument("--byo", action="store_true", help=f"read {BYO_DIR}/NAME.conf instead of stdin")
    sub.add_parser("down", help="stop a tunnel").add_argument("name")
    sub.add_parser("status", help="list tunnels (no root needed)").add_argument("name", nargs="?")
    sub.add_parser("probe", help="report the helper protocol and the OpenVPN version (no root needed)")
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        # The tools below are run by name. sudoers usually pins PATH (secure_path), but a rule limited to this helper is
        # only a limit if the helper does not depend on that.
        os.environ["PATH"] = ROOT_PATH
    previous: dict[int, Any] = {}
    try:
        if args.command == "up":
            check_name(args.name)
            text = None
            if not args.byo:
                raw = sys.stdin.buffer.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise Refused("the profile is larger than 1 MiB")
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise Refused("the profile is not valid UTF-8") from exc
            # The caller hanging up (its timeout, a closed session) must not leave a half-checked tunnel behind. The
            # handler only takes a note; `up` acts on it between its steps.
            previous = {sig: signal.signal(sig, _interrupt) for sig in _SIGNALS}
            try:
                result = cmd_up(args.name, byo=args.byo, profile_text=text)
            except Interrupted as exc:
                result = {"status": "error", "message": f"interrupted: {exc}"}
        elif args.command == "down":
            result = cmd_down(check_name(args.name))
        elif args.command == "status":
            result = cmd_status(args.name)
        else:
            result = cmd_probe()
    except Refused as exc:
        result = {"status": "error", "message": str(exc)}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
    try:
        # Out before the old handlers are back: a second copy of a signal that found them restored ended the process
        # with the answer still in the buffer.
        print(json.dumps(result), flush=True)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
