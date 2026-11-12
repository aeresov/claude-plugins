# SPDX-License-Identifier: AGPL-3.0-only
"""Host DNS check: do ordinary programs resolve names through systemd-resolved?

openvpn3-service-netcfg hands the tunnel's DNS servers and domains to systemd-resolved (setup-checklist check 4).
Programs only see that split DNS if they ask systemd-resolved: via ``resolve`` on the nsswitch ``hosts:`` line, or
via /etc/resolv.conf naming the 127.0.0.53 stub. When Tailscale, a VPN client or NetworkManager owns /etc/resolv.conf
instead, private names fail or come back as 0.0.0.0 while ``resolvectl query`` looks fine.

On a host without openvpn3 (the OpenVPN 2 backend, e.g. Amazon Linux 2023) nothing hands tunnel DNS to systemd-resolved:
names resolve through the host's own resolver, tunnel up or down. There the question is only what programs get for the
host — a private address is what the tunnel's routes need — and no resolver change is ever suggested.

    python3 dnscheck.py [--connected] [--backend openvpn3|openvpn2] [HOSTNAME | --url-stdin]

Without a hostname it reads the host's resolver setup and says whether programs reach systemd-resolved. With one
(tunnel up) it also compares systemd-resolved's answer with what programs get from ``getent ahosts``. ``--url-stdin``
takes the host from a URL on stdin (``rediss://user:pass@host:port``) without ever printing the URL. ``--connected``
says the tunnel is up for sure (the skill passes it after connecting), so a host no tunnel domain covers is still judged
by what programs get instead of being put down to a tunnel that might be down. ``--backend`` says which VPN client the
tunnel runs on (the skill knows it from the connect result); without it, a host that has openvpn3 installed is taken to
use it.
Exit status: 0 OK, 1 FAIL, 3 WARN, 2 bad usage. Read-only: it prints fixes for a human to run and never runs them.

Standard library only, Python 3.9 syntax (Amazon Linux 2023's system python3), no package-relative imports: doctor, setup
and the vpn-on-demand skill run it by path with the system python3, outside the server's venv. (``python3 -m openvpn_mcp.dnscheck`` would import the
package ``__init__`` and with it the MCP server.)
"""

from __future__ import annotations

import argparse
import ctypes.util
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

RESOLV_CONF = Path("/etc/resolv.conf")
NSSWITCH_CONF = Path("/etc/nsswitch.conf")
HOSTS_FILE = Path("/etc/hosts")
NETCFG_JSON = Path("/var/lib/openvpn3/netcfg.json")
RESOLVED_RUN_DIR = Path("/run/systemd/resolve")
SYS_CLASS_NET = Path("/sys/class/net")
README = Path(__file__).resolve().parents[4] / "README.md"

STUB_ADDRESS = "127.0.0.53"
# Caps getent sitting through glibc's resolver retries: 5 s x 2 attempts per nameserver, 20 s with Tailscale's two.
LOOKUP_TIMEOUT_SECS: float = 15.0

OK, WARN, FAIL = "OK", "WARN", "FAIL"
EXIT_CODES = {OK: 0, FAIL: 1, WARN: 3}

# Suffixes, not full paths: the symlink may be relative (../run/...) and usrmerge puts the static file under /lib too.
_RESOLVED_TARGETS = (
    ("/run/systemd/resolve/stub-resolv.conf", "systemd-resolved stub"),
    ("/lib/systemd/resolv.conf", "systemd-resolved static stub"),
    ("/run/systemd/resolve/resolv.conf", "systemd-resolved uplink file (lists upstream servers, skips per-link routing)"),
)

UNKNOWN_OWNER = "unknown (no owner comment; a VPN client such as Cisco Secure Client writes files like this)"

# What "the name resolved to somewhere private" means on a host whose tunnel only adds routes: the ranges a VPN routes.
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))

_HOSTNAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}$")
_LINK_LINE = re.compile(r"^Link \d+ \(([^)]+)\):(.*)$")


@dataclass
class ResolvConf:
    owner: str
    nameservers: list[str]
    symlink: bool

    @property
    def is_stub(self) -> bool:
        return bool(self.nameservers) and all(ns == STUB_ADDRESS for ns in self.nameservers)


@dataclass
class Lookup:
    addrs: list[str] = field(default_factory=list)
    error: str | None = None
    links: list[str] = field(default_factory=list)


@dataclass
class Report:
    facts: list[tuple[str, str]]
    verdict: str
    reason: str
    fix: list[str] = field(default_factory=list)


# --- parsing (pure) ---------------------------------------------------------------------------------------------


def resolv_conf_owner(link_target: str | None, text: str) -> str:
    if link_target is not None:
        for suffix, owner in _RESOLVED_TARGETS:
            if link_target.endswith(suffix):
                return owner
        return f"symlink to {link_target}"
    header = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            break
        header.append(stripped.lower())
    comments = "\n".join(header)
    # Tailscale first: its own header links to .../resolvconf-overwrite, which a plain "resolvconf" match would
    # misread. That exact misread is how tailscaled gets stuck (tailscale/tailscale#19062).
    if "generated by tailscale" in comments:
        return "tailscale"
    if "networkmanager" in comments:
        return "NetworkManager"
    if "systemd-resolved" in comments:
        return "copy of a systemd-resolved file"
    if "resolvconf" in comments:
        return "resolvconf"
    return UNKNOWN_OWNER


def parse_nameservers(text: str) -> list[str]:
    servers = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            servers.append(fields[1])
    return servers


def nsswitch_hosts(text: str) -> str | None:
    """The ``hosts:`` line's sources and actions, comments stripped; None if there is none. glibc uses the last one."""
    found = None
    for line in text.splitlines():
        if match := re.match(r"hosts\s*:(.*)", line.split("#", 1)[0].strip()):
            found = " ".join(match.group(1).split())
    return found


def nss_services(hosts_line: str) -> list[str]:
    # Actions like "[NOTFOUND=return]" may contain spaces; drop them before splitting.
    return re.sub(r"\[[^\]]*\]", " ", hosts_line).split()


def resolve_before_dns(services: list[str]) -> bool:
    if "resolve" not in services:
        return False
    return "dns" not in services or services.index("resolve") < services.index("dns")


def netcfg_systemd_resolved(text: str) -> str | None:
    # netcfg.json carries // comments, so it isn't strict JSON.
    match = re.search(r'"systemd_resolved"\s*:\s*([^\s,}]+)', text)
    return match.group(1) if match else None


def hosts_pins(text: str, name: str) -> list[str]:
    wanted = name.lower().rstrip(".")
    pins = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and wanted in (f.lower().rstrip(".") for f in fields[1:]):
            pins.append(fields[0])
    return pins


def parse_link_domains(out: str) -> dict[str, list[str]]:
    """``resolvectl domain`` output → {link name: domains}, links without domains left out."""
    links = {}
    for line in out.splitlines():
        if (match := _LINK_LINE.match(line.strip())) and (domains := match.group(2).split()):
            links[match.group(1)] = domains
    return links


def route_for(host: str, links: dict[str, list[str]]) -> tuple[str, str] | None:
    """The (link, domain) systemd-resolved routes ``host`` to: the matching domain with the most labels, or None."""
    name = host.lower().rstrip(".")
    best: tuple[str, str] | None = None
    for link, domains in links.items():
        for raw in domains:
            domain = raw.lstrip("~").lower().rstrip(".")
            if not domain:
                continue  # "~." claims everything unmatched; it isn't a tunnel's split-DNS domain
            if (name == domain or name.endswith("." + domain)) and (best is None or domain.count(".") > best[1].count(".")):
                best = (link, domain)
    return best


def parse_resolvectl_query(out: str) -> tuple[list[str], list[str]]:
    """``resolvectl query --legend=no`` output → (addresses, links). Lines look like ``name: 10.0.0.1 -- link: tun0``."""
    addrs: list[str] = []
    links: list[str] = []
    for line in out.splitlines():
        body, _, meta = line.partition(" -- ")
        for token in body.split():
            if _is_ip(token) and token not in addrs:
                addrs.append(token)
        if (match := re.search(r"link:\s*(\S+)", meta)) and match.group(1) not in links:
            links.append(match.group(1))
    return addrs, links


def parse_getent_ahosts(out: str) -> tuple[list[str], list[str]]:
    """``getent ahosts`` output → (addresses, []). Each address repeats once per socket type."""
    addrs: list[str] = []
    for line in out.splitlines():
        fields = line.split()
        if fields and _is_ip(fields[0]) and fields[0] not in addrs:
            addrs.append(fields[0])
    return addrs, []


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _unspecified(addrs: list[str]) -> list[str]:
    return [a for a in addrs if _is_ip(a) and ipaddress.ip_address(a).is_unspecified]


def _private(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return any(ip in net for net in _PRIVATE_NETWORKS if net.version == ip.version)


def _this_machine(addrs: list[str]) -> list[str]:
    """Answers that send a client to this very host: 0.0.0.0 / :: (what filtering resolvers return) and loopback."""
    return [a for a in addrs if _is_ip(a) and (ipaddress.ip_address(a).is_unspecified or ipaddress.ip_address(a).is_loopback)]


# --- host I/O (monkeypatched in tests) --------------------------------------------------------------------------


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=LOOKUP_TIMEOUT_SECS, check=False)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_resolv_conf() -> ResolvConf:
    target = None
    if RESOLV_CONF.is_symlink():
        target = os.path.normpath(os.path.join(os.path.dirname(RESOLV_CONF), os.readlink(RESOLV_CONF)))
    text = _read(RESOLV_CONF) or ""
    return ResolvConf(owner=resolv_conf_owner(target, text), nameservers=parse_nameservers(text), symlink=target is not None)


def nss_resolve_installed() -> bool:
    return ctypes.util.find_library("nss_resolve") is not None


def _stub_answers() -> bool:
    """One DNS question to 127.0.0.53: does anything answer there? Any reply counts, also a refusal."""
    query = b"\x6f\x64\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x09localhost\x00\x00\x01\x00\x01"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.sendto(query, (STUB_ADDRESS, 53))
            return len(sock.recv(512)) >= 12
    except OSError:  # nothing listening (refused), no reply (timeout), no loopback
        return False


def _stub_listener_setting() -> str | None:
    """systemd-resolved's own word on its stub listener — "yes", "udp", "tcp" or "no" — or None where it can't be asked:
    no busctl, no system bus, a systemd too old to have the property."""
    try:
        proc = _run(["busctl", "get-property", "org.freedesktop.resolve1", "/org/freedesktop/resolve1", "org.freedesktop.resolve1.Manager", "DNSStubListener"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.fullmatch(r's "(yes|udp|tcp|no)"', proc.stdout.strip()) if proc.returncode == 0 else None
    return match.group(1) if match else None


def stub_listener_available() -> bool:
    """Whether systemd-resolved answers DNS on 127.0.0.53. With DNSStubListener=no (Amazon Linux's default) recent
    systemd writes stub-resolv.conf as a pointer to the uplink file, so the stub address isn't in it; older systemd still
    names it. Then systemd-resolved is asked for the setting, and only where it can't say does a DNS probe decide: an
    unanswered probe alone also happens to a check run in a network sandbox or while the daemon restarts, on a host
    whose stub is fine. Pointing /etc/resolv.conf at a stub nothing listens on leaves a host with no working resolver."""
    if STUB_ADDRESS not in parse_nameservers(_read(RESOLVED_RUN_DIR / "stub-resolv.conf") or ""):
        return False
    setting = _stub_listener_setting()
    if setting is not None:
        return setting in ("yes", "udp")  # glibc asks over UDP
    return _stub_answers()


def openvpn3_present() -> bool:
    """Whether a VPN client that hands tunnel DNS to systemd-resolved is installed. Without openvpn3 the plugin's
    OpenVPN 2 backend is in use, which leaves DNS alone."""
    return shutil.which("openvpn3") is not None or NETCFG_JSON.parent.is_dir()


def _is_virtual(link: str) -> bool:
    return not (SYS_CLASS_NET / link / "device").exists()


def off_tunnel(addrs: list[str]) -> list[tuple[str, str]]:
    """(address, interface) for each private address this host would reach over a hardware NIC rather than a tunnel:
    a VPN that routes the very range the host itself sits in leaves the host's own subnet local, so a private answer
    inside it never enters the tunnel. Addresses whose route can't be read are left out."""
    found = []
    for addr in addrs:
        if not _private(addr):
            continue
        try:
            proc = _run(["ip", "-j", "route", "get", addr])
            dev = json.loads(proc.stdout)[0].get("dev") if proc.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, AttributeError, TypeError):
            continue
        if isinstance(dev, str) and dev and not _is_virtual(dev):
            found.append((addr, dev))
    return found


def link_domains() -> tuple[dict[str, list[str]] | None, str | None]:
    """Per-link DNS domains from systemd-resolved, else (None, why). A None reason means systemd-resolved isn't in use here."""
    try:
        proc = _run(["resolvectl", "domain"])
    except FileNotFoundError:
        return None, None
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = f"resolvectl domain: {exc}"
    else:
        if proc.returncode == 0:
            return parse_link_domains(proc.stdout), None
        error = (proc.stderr.strip() or f"resolvectl domain exited {proc.returncode}").splitlines()[-1]
    # resolvectl exists but failed (no system bus, hung daemon): that's only "not in use" if resolved has no runtime dir.
    return None, (error if RESOLVED_RUN_DIR.is_dir() else None)


def _lookup(argv: list[str], parse: Callable[[str], tuple[list[str], list[str]]], not_found_rc: int | None = None) -> Lookup:
    try:
        proc = _run(argv)
    except OSError as exc:
        return Lookup(error=f"{argv[0]} failed to start: {exc.strerror or exc}")
    except subprocess.TimeoutExpired:
        return Lookup(error=f"no answer within {LOOKUP_TIMEOUT_SECS:g}s")
    if proc.returncode != 0:
        if proc.returncode == not_found_rc:
            return Lookup(error="no answer (not found, or the resolver in /etc/resolv.conf didn't respond)")
        lines = (proc.stderr.strip() or proc.stdout.strip() or f"{argv[0]} exited {proc.returncode}").splitlines()
        return Lookup(error=lines[-1])
    addrs, links = parse(proc.stdout)
    return Lookup(addrs=addrs, error=None if addrs else "no addresses in the answer", links=links)


def resolved_lookup(host: str) -> Lookup:
    # --synthesize=no: skip /etc/hosts, so a pin there can't make systemd-resolved agree with itself.
    return _lookup(["resolvectl", "query", "--legend=no", "--synthesize=no", host], parse_resolvectl_query)


def nss_lookup(host: str) -> Lookup:
    # getent ahosts goes through getaddrinfo(3) exactly like redis-cli, psql, curl or Python do. Exit 2 = no answer.
    return _lookup(["getent", "ahosts", host], parse_getent_ahosts, not_found_rc=2)


# --- diagnosis --------------------------------------------------------------------------------------------------


def layer2_fix(resolv: ResolvConf, *, stub: bool = True, nss_resolve: bool = False) -> list[str]:
    nsswitch_edit = '"resolve [!UNAVAIL=return]" before "dns" on the hosts: line of /etc/nsswitch.conf'
    if nss_resolve:
        nss_lines = [f"      add {nsswitch_edit}    # libnss_resolve is already installed; only the entry is missing"]
    else:
        nss_lines = [
            f"      sudo apt install libnss-resolve    # adds {nsswitch_edit.replace(' on the hosts: line of', ' in')}",
            f"      (Fedora, RHEL and Amazon Linux ship the library with systemd: add {nsswitch_edit} instead)",
        ]
    if not stub:
        # No stub listener (DNSStubListener=no): the stub symlink would point every program at an address nothing
        # answers on. nss-resolve reaches systemd-resolved over its socket and doesn't need the listener.
        return [
            "Fix (needs root, so a human runs it; the plugin never does). Nothing answers on systemd-resolved's 127.0.0.53 stub listener on this",
            "host, so do NOT point /etc/resolv.conf at stub-resolv.conf here: nothing would answer. Use nss-resolve instead; it covers programs",
            "that resolve through glibc, not dig or Go binaries built without cgo:",
            *nss_lines,
            f"Details: {README} (Troubleshooting).",
        ]
    lines = [
        "Fix (needs root, so a human runs it; the plugin never does). Either:",
        "  - point /etc/resolv.conf at the systemd-resolved stub, so every program on this host uses the tunnel's split DNS:",
        "      sudo ln -sfn ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf",
    ]
    if resolv.owner == "tailscale":
        lines += [
            "    then let Tailscale re-detect it; it moves its own DNS into systemd-resolved:",
            "      sudo rm -f /etc/resolv.pre-tailscale-backup.conf",
            "      sudo systemctl restart tailscaled",
        ]
    elif resolv.owner == "NetworkManager":
        lines.append("    NetworkManager wrote the file: first set dns=systemd-resolved under [main] in /etc/NetworkManager/conf.d/, then restart it.")
    elif resolv.owner == UNKNOWN_OWNER:
        lines.append("    If a VPN client wrote the file, disconnect it first; it puts its saved copy back when it disconnects.")
    lines += [
        "  - or use nss-resolve; this covers programs that resolve through glibc, not dig or Go binaries built without cgo:",
        *nss_lines,
        f"Details: {README} (Troubleshooting).",
    ]
    return lines


def decide_host_resolver(facts: list[tuple[str, str]], host: str | None, nss: Lookup | None, pins: list[str], direct: list[tuple[str, str]] | None = None) -> Report:
    """The verdict on a host where no VPN client hands tunnel DNS to systemd-resolved (the OpenVPN 2 backend): the tunnel
    only adds routes, so a name is fine when the host's own resolver returns a private address for it. No fix is ever
    printed here — nothing about this host's resolver setup is wrong, and changing it is how a remote machine gets lost."""
    facts.append(("vpn dns", "left alone: no openvpn3 on this host, and the OpenVPN 2 backend doesn't touch DNS"))
    if host is None or _is_ip(host):
        prefix = f"{host} is an IP address, no DNS involved. " if host else ""
        return Report(
            facts,
            OK,
            prefix + "The VPN leaves DNS alone on this host: names resolve through the host's own resolver, tunnel up or down. "
            "Run this with a private hostname to see what programs get for it.",
        )
    assert nss is not None
    facts.append(("lookup", f"{host} -> the host's own resolver (the tunnel's DNS isn't used on this host)"))
    facts.append(("  programs", (", ".join(nss.addrs) if nss.addrs else f"none: {nss.error}") + (f" (/etc/hosts pins {', '.join(pins)})" if pins else "")))
    own_dns = "The OpenVPN 2 backend doesn't use the VPN's DNS, so the name has to resolve through this host's own resolver."
    if _this_machine(nss.addrs):
        source = "/etc/hosts pins it there" if _this_machine(pins) else "the resolver this host uses filtered the private answer"
        return Report(
            facts,
            FAIL,
            f"Programs get {_this_machine(nss.addrs)[0]} for {host} ({source}). A client would connect to this machine instead; "
            f"a TLS client can hang against a local service. {own_dns}",
        )
    if not nss.addrs:
        return Report(facts, FAIL, f"Programs can't resolve {host} ({nss.error}). {own_dns} A name only the private network's DNS knows won't resolve here.")
    if pins and set(pins) & set(nss.addrs):
        return Report(
            facts,
            WARN,
            f"Programs get {nss.addrs[0]} for {host} because /etc/hosts pins it. Other names still go to the resolver, including the node names a "
            "cluster endpoint (MemoryDB, ElastiCache cluster mode) hands its clients, and the pin goes stale if the endpoint moves.",
        )
    if not all(_private(a) for a in nss.addrs):
        public = next(a for a in nss.addrs if not _private(a))
        return Report(
            facts,
            WARN,
            f"Programs get {public} for {host}, which is not a private address, so the tunnel's routes don't carry it. If the resource is private, its "
            f"name needs the private network's own DNS. {own_dns}",
        )
    if direct:
        addr, dev = direct[0]
        facts.append(("  route", f"{addr} -> {dev} (not a tunnel)"))
        return Report(
            facts,
            FAIL,
            f"Programs get {addr} for {host}, a private address, but with the tunnel up this host still reaches {addr} directly on {dev}: it lies in a "
            "network the host itself is attached to (or the tunnel carries no route for it), so a client would talk to whatever has that address here.",
        )
    return Report(facts, OK, f"Programs get {nss.addrs[0]} for {host}, a private address, from this host's own resolver; the tunnel's DNS isn't needed for it.")


def decide(
    *,
    resolv: ResolvConf,
    hosts_line: str | None,
    nss_resolve: bool,
    links: dict[str, list[str]] | None,
    resolved_error: str | None = None,
    host: str | None = None,
    connected: bool = False,
    resolved: Lookup | None = None,
    nss: Lookup | None = None,
    pins: list[str] | None = None,
    netcfg: str | None = None,
    tunnel_dns: bool = True,
    stub: bool = True,
    direct: list[tuple[str, str]] | None = None,
) -> Report:
    """``tunnel_dns``: a client that puts tunnel DNS into systemd-resolved (openvpn3) is installed. ``stub``: systemd-resolved
    answers on 127.0.0.53. Both default to the Debian/Ubuntu + openvpn3 host this check was first written for."""
    pins = pins or []
    services = nss_services(hosts_line or "")
    via_nss = resolve_before_dns(services) and nss_resolve
    reaches = (resolv.is_stub and stub) or via_nss

    kind = "symlink" if resolv.symlink else "regular file"
    facts = [
        ("resolv.conf", f"{resolv.owner} ({kind}); nameservers {', '.join(resolv.nameservers) or 'none'}"),
        ("nsswitch", f"hosts: {hosts_line}" if hosts_line is not None else "no hosts: line"),
        ("nss-resolve", "installed" if nss_resolve else "not installed"),
    ]
    if not tunnel_dns:
        return decide_host_resolver(facts, host, nss, pins, direct)
    facts.append(("netcfg", f"systemd_resolved = {netcfg}" if netcfg is not None else "no systemd_resolved setting (see check 4)"))
    if links is None and resolved_error is None:
        return Report(facts, OK, "SKIP: systemd-resolved isn't running here, so DNS over the tunnel is out of scope on this host.")
    if resolv.is_stub and stub:
        facts.append(("programs", "query systemd-resolved (resolv.conf is the stub)"))
    elif via_nss:
        facts.append(("programs", "query systemd-resolved through nss-resolve; tools that read resolv.conf themselves (dig, Go without cgo) don't"))
    else:
        facts.append(("programs", f"do NOT query systemd-resolved: glibc asks {', '.join(resolv.nameservers) or 'no nameserver'} from resolv.conf"))
    if "resolve" in services and not nss_resolve:
        facts.append(("note", "nsswitch lists resolve, but libnss_resolve.so.2 isn't installed, so glibc skips it"))
    if resolv.is_stub and not stub:
        facts.append(
            (
                "note",
                f"resolv.conf names {STUB_ADDRESS}, but nothing answers there: systemd-resolved's stub listener is off (DNSStubListener=no), or this "
                "check ran where the host's loopback and system bus are out of reach (a command sandbox; rerun it outside one)",
            )
        )
    fix = [] if reaches else layer2_fix(resolv, stub=stub, nss_resolve=nss_resolve)
    if links is None:
        return Report(facts, WARN, f"Couldn't query systemd-resolved ({resolved_error}), so this can't tell what the tunnel's DNS would answer.", fix)
    facts.append(("link domains", "; ".join(f"{link}: {' '.join(ds)}" for link, ds in links.items()) or "none (is the tunnel up?)"))

    if host is None or _is_ip(host):
        prefix = f"{host} is an IP address, no DNS involved. " if host else ""
        if reaches:
            return Report(facts, OK, prefix + "Programs reach systemd-resolved, so they see the tunnel's split DNS.")
        domains = ", ".join(d for ds in links.values() for d in ds if d.lstrip("~").strip(".")) or "the tunnel's DNS domains"
        return Report(
            facts,
            WARN,
            prefix + f"Programs don't query systemd-resolved, so names under {domains} resolve only if the resolver in "
            "/etc/resolv.conf forwards them there. Run this with a private hostname while the tunnel is up for a definite answer.",
            fix,
        )

    route = route_for(host, links)
    if route is None and not connected:
        facts.append(("lookup", f"{host} -> no link's DNS domain covers it; not compared"))
        return Report(
            facts,
            WARN,
            f"No link's DNS domain covers {host}: the tunnel is down, or the VPN doesn't push a DNS domain for it (e.g. "
            "`dhcp-option DOMAIN amazonaws.com` in the .ovpn). Connect and rerun for the comparison.",
            fix,
        )
    if route is None:
        # Tunnel up, yet no split-DNS domain covers the host (none pushed, or a dns-scope=global "~." tunnel): the
        # tunnel's DNS isn't asked for it specifically, so judge by what programs get from the general resolvers.
        assert nss is not None
        facts.append(("lookup", f"{host} -> no link's DNS domain covers it, so the tunnel's DNS isn't asked for it specifically"))
        facts.append(("  programs", (", ".join(nss.addrs) if nss.addrs else f"none: {nss.error}") + (f" (/etc/hosts pins {', '.join(pins)})" if pins else "")))
        unrouted = (
            f"No link's DNS domain covers {host}, so the tunnel's DNS isn't asked for it; the VPN has to push a DNS domain for it "
            "(e.g. `dhcp-option DOMAIN amazonaws.com` in the .ovpn)."
        )
        if _unspecified(nss.addrs):
            return Report(facts, FAIL, f"Programs get {_unspecified(nss.addrs)[0]} for {host}. {unrouted}", fix)
        if not nss.addrs:
            return Report(facts, FAIL, f"Programs can't resolve {host} ({nss.error}). {unrouted}", fix)
        pinned = " because /etc/hosts pins it" if pins else ""
        return Report(facts, WARN, f"Programs get {nss.addrs[0]} for {host}{pinned}, from the general resolvers. {unrouted}", fix)
    assert resolved is not None and nss is not None
    facts.append(("lookup", f"{host} -> {route[0]} (domain {route[1]})"))
    facts.append(("  resolved", ", ".join(resolved.addrs) + (f" via {', '.join(resolved.links)}" if resolved.links else "") if resolved.addrs else f"none: {resolved.error}"))
    facts.append(("  programs", (", ".join(nss.addrs) if nss.addrs else f"none: {nss.error}") + (f" (/etc/hosts pins {', '.join(pins)})" if pins else "")))
    r, n = resolved.addrs, nss.addrs

    if _unspecified(r):
        return Report(
            facts,
            FAIL,
            f"systemd-resolved itself answers {_unspecified(r)[0]} for {host}, from the DNS on {route[0]}. Check the name and that this is the right environment's VPN.",
            fix,
        )
    if _unspecified(n):
        source = "/etc/hosts pins it there" if _unspecified(pins) else "the resolver programs use filtered the private answer"
        return Report(
            facts,
            FAIL,
            f"Programs get {_unspecified(n)[0]} for {host} ({source})"
            + (f", while systemd-resolved says {r[0]}" if r else "")
            + ". A client would connect to this machine instead; a TLS client can hang against a local service.",
            fix,
        )
    if not n:
        if r:
            return Report(facts, FAIL, f"Programs can't resolve {host} ({nss.error}), but systemd-resolved can ({r[0]}).", fix)
        return Report(
            facts,
            FAIL,
            f"{host} doesn't resolve on either path. It may be the wrong name or the wrong environment's VPN, or the tunnel's DNS isn't "
            "answering (`resolvectl dns` should list the VPN's DNS servers on the tunnel link).",
            fix,
        )
    if not r:
        pinned = " because /etc/hosts pins it" if pins else ""
        return Report(facts, WARN, f"systemd-resolved can't resolve {host} ({resolved.error}), but programs get {n[0]}{pinned}.", fix)
    if not set(r) & set(n):
        if pins:
            return Report(facts, FAIL, f"/etc/hosts pins {host} to {pins[0]}, but DNS now says {r[0]}: the pin is stale. Remove it from /etc/hosts.", fix)
        return Report(facts, FAIL, f"Programs get {n[0]} for {host}, but systemd-resolved (via {route[0]}) gets {r[0]}.", fix)
    if pins and not reaches:
        return Report(
            facts,
            WARN,
            f"{host} works only because /etc/hosts pins it. Other names under {route[1]} still bypass the tunnel's DNS, including the node "
            "names a cluster endpoint (MemoryDB, ElastiCache cluster mode) hands its clients, and the pin goes stale if the endpoint moves.",
            fix,
        )
    same = "" if reaches else " The resolver in /etc/resolv.conf gave the same answer this time."
    return Report(facts, OK, f"Programs and systemd-resolved agree on {host} ({n[0]}).{same}")


def check(host: str | None, connected: bool = False, backend: str | None = None) -> Report:
    # Only openvpn3 hands tunnel DNS to systemd-resolved. A host can have both clients installed, so the caller's word
    # on which one the tunnel runs on beats what is found on disk.
    tunnel_dns = backend == "openvpn3" if backend else openvpn3_present()
    links, resolved_error = link_domains()
    resolved = nss = None
    direct: list[tuple[str, str]] = []
    if host is not None and not _is_ip(host):
        if not tunnel_dns:
            # DNS doesn't depend on the tunnel here, so what programs get is the whole answer, connected or not.
            nss = nss_lookup(host)
            if connected:
                # ...but whether that answer is behind the tunnel does: only worth asking once the tunnel is known up.
                direct = off_tunnel(nss.addrs)
        elif links is not None and route_for(host, links) is not None:
            resolved, nss = resolved_lookup(host), nss_lookup(host)
        elif links is not None and connected:
            nss = nss_lookup(host)
    netcfg_text = _read(NETCFG_JSON)
    return decide(
        resolv=read_resolv_conf(),
        hosts_line=nsswitch_hosts(_read(NSSWITCH_CONF) or ""),
        nss_resolve=nss_resolve_installed(),
        links=links,
        resolved_error=resolved_error,
        host=host,
        connected=connected,
        resolved=resolved,
        nss=nss,
        pins=hosts_pins(_read(HOSTS_FILE) or "", host) if host else [],
        netcfg=netcfg_systemd_resolved(netcfg_text) if netcfg_text is not None else None,
        tunnel_dns=tunnel_dns,
        stub=stub_listener_available(),
        direct=direct,
    )


def render(report: Report) -> str:
    width = max(len(label) for label, _ in report.facts)
    lines = [f"{label.ljust(width)}  {value}" for label, value in report.facts]
    lines += ["", f"{report.verdict}  {report.reason}"]
    if report.fix:
        lines += ["", *report.fix]
    return "\n".join(lines)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse quotes the offending argument back; a URL there may carry credentials, so say what's wanted instead.
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: pass one bare hostname (no scheme, credentials, port or path), or --url-stdin with the URL on stdin\n")


def host_from_url(url: str) -> str | None:
    """The host of ``scheme://user:pass@host:port/path``. Splits on the last '@' first, so a password holding an
    unencoded '#', '?' or '/' can't be mistaken for the host (urlsplit alone would)."""
    rest = url.strip().split("://", 1)[-1].rpartition("@")[2]
    try:
        return urllib.parse.urlsplit("//" + rest).hostname
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="dnscheck.py",
        description="Check that programs resolve names through systemd-resolved, and so see the tunnel's split DNS. Read-only.",
        epilog="Exit status: 0 OK, 1 FAIL, 3 WARN, 2 bad usage.",
    )
    parser.add_argument("hostname", nargs="?", help="a private hostname behind the VPN; compared on both lookup paths (needs the tunnel up)")
    parser.add_argument("--url-stdin", action="store_true", help="read a URL from stdin and check its host; the URL is never printed")
    parser.add_argument("--connected", action="store_true", help="the tunnel is up (the skill passes this after connecting)")
    parser.add_argument("--backend", choices=("openvpn3", "openvpn2"), help="the VPN client the tunnel runs on, from the connect result; default: openvpn3 if installed")
    args = parser.parse_args(argv)
    host = args.hostname
    if args.url_stdin:
        if host is not None:
            parser.error("both")
        host = host_from_url(sys.stdin.read())
        if host is None:
            parser.error("no host in the URL")
    if host is not None and not (_is_ip(host) or _HOSTNAME.fullmatch(host)):
        parser.error("not a hostname")
    report = check(host, connected=args.connected, backend=args.backend) if args.backend else check(host, connected=args.connected)
    print(render(report))
    return EXIT_CODES[report.verdict]


if __name__ == "__main__":
    sys.exit(main())
