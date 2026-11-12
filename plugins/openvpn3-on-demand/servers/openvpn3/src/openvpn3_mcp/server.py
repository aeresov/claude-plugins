# SPDX-License-Identifier: AGPL-3.0-only
#
# Copyright (C) 2026 aeresov
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 as published
# by the Free Software Foundation. See the LICENSE file at the repo root.

"""VPN-on-demand MCP server. Two backends behind the same four tools: openvpn3-linux over D-Bus via the ``openvpn3``
Python module, and — on hosts that have no openvpn3-linux, such as Amazon Linux 2023 — the distribution's OpenVPN 2.6
run in transient systemd units (``ovpn2.py``)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import ovpn2

__version__ = _pkg_version("openvpn3-mcp")

try:
    # Both ship as system packages (python3-dbus, openvpn3-client) reached via
    # --system-site-packages; no type checker or venv-only environment can see them.
    import dbus as _dbus  # type: ignore[import-not-found]  # ty: ignore[unresolved-import]
    import openvpn3 as _openvpn3  # type: ignore[import-not-found]  # ty: ignore[unresolved-import]
except ImportError:
    # Not an error by itself: a host without openvpn3-linux can still have the OpenVPN 2 backend.
    _dbus = _openvpn3 = None
dbus: Any = _dbus
openvpn3: Any = _openvpn3

BACKEND_ENV = "OPENVPN3_OD_BACKEND"


def _select_backend() -> str:
    """openvpn3 where its Python modules import, else OpenVPN 2 where the binary is installed. ``OPENVPN3_OD_BACKEND``
    (``openvpn3`` / ``openvpn2``) forces one, for a host that has both."""
    wanted = os.environ.get(BACKEND_ENV, "").strip().lower()
    have = {"openvpn3": dbus is not None, "openvpn2": ovpn2.available()}
    if wanted:
        if wanted not in have:
            sys.exit(f"openvpn3-mcp: {BACKEND_ENV}={wanted!r} is not a backend; use 'openvpn3' or 'openvpn2'.")
        if not have[wanted]:
            sys.exit(f"openvpn3-mcp: {BACKEND_ENV}={wanted} was asked for, but that backend isn't installed on this host.")
        return wanted
    if have["openvpn3"]:
        return "openvpn3"
    if shutil.which("openvpn3") is not None:
        # openvpn3-linux is on this host but its Python modules don't import here: a broken setup (python3-dbus missing,
        # a venv without system site-packages), not a host that should quietly be driven through OpenVPN 2 instead.
        sys.exit(
            "openvpn3-mcp: openvpn3 is installed, but 'dbus' and/or 'openvpn3' can't be imported. Install the 'python3-dbus' and 'openvpn3-client' "
            "system packages; the server's venv must see system site-packages (delete servers/openvpn3/.venv to have it rebuilt). "
            f"To use OpenVPN 2 on this host instead, set {BACKEND_ENV}=openvpn2."
        )
    if have["openvpn2"]:
        return "openvpn2"
    print(
        "openvpn3-mcp: no VPN backend found. Install either openvpn3-linux ('openvpn3-client' plus 'python3-dbus'; the venv must see "
        "system site-packages) or OpenVPN 2.6 (the distribution's 'openvpn' package, e.g. `sudo dnf install openvpn` on Amazon Linux 2023).",
        file=sys.stderr,
    )
    sys.exit(1)


BACKEND = _select_backend()

mcp = FastMCP("openvpn3")

# Split-DNS baseline: only the VPN's pushed domains go to its DNS servers, so inside systemd-resolved the tunnel doesn't
# fight Tailscale / mDNS / other VPNs. Programs only benefit if they query systemd-resolved (dnscheck.py). Caller wins on collision.
_DEFAULT_OVERRIDES: dict[str, Any] = {"dns-scope": "tunnel"}

# openvpn3 failure surface: D-Bus method errors + RuntimeError from manager __ping().
_DBUS_ERRORS: tuple[type[BaseException], ...] = (dbus.exceptions.DBusException, RuntimeError) if dbus is not None else (RuntimeError,)

# OpenVPN 2 failure surface: the helper refusing a request, sudo or systemd not answering, a tool missing or hanging.
_OVPN2_ERRORS = (ovpn2.helper.Refused, ovpn2.BackendError, OSError, ValueError, subprocess.TimeoutExpired)

# How long vpn_disconnect waits for the session to clear before reporting `session_cleared=False`.
_DISCONNECT_TIMEOUT_SECS: float = 5.0

# How long we poll session status after `Connect()` waiting for CONN_CONNECTED before giving up.
# Bounds the whole handshake (transport + TLS + route/DNS push); expiry also tears down a session this call
# started, so keep it generous enough for a TCP-fallback or high-latency link.
_CONNECT_TIMEOUT_SECS: float = 30.0

# StatusMinor names that mean the tunnel won't come up — fail fast instead of waiting for the timeout.
_CONNECT_FAILURE_MINORS: frozenset[str] = frozenset({"CONN_FAILED", "CONN_AUTH_FAILED", "CONN_DISCONNECTED"})

# StatusMinor names of a session still on its way up — an existing one in these states is waited for, not reported.
_CONNECT_PENDING_MINORS: frozenset[str] = frozenset({"CONN_CONNECTING", "CONN_RECONNECTING", "CONN_RESUMING"})


# Tagged-union result models, discriminated on `status`; FastMCP emits each tool's union as its outputSchema.


BackendName = Annotated[
    Literal["openvpn3", "openvpn2"],
    Field(description="Which VPN client this host uses: openvpn3-linux over D-Bus, or OpenVPN 2 in a systemd unit (hosts without openvpn3)."),
]


class SessionView(BaseModel):
    path: Annotated[str, Field(description="Handle of the session: its D-Bus object path (openvpn3) or its systemd unit (openvpn2).")]
    config_name: Annotated[str, Field(description="Profile name the session runs under.")]
    status: Annotated[
        str,
        Field(description="openvpn3: 'MAJOR/MINOR' from the session manager, plus ': message' when one is set. openvpn2: 'STATE: detail' from the tunnel's unit."),
    ]


class VpnStatusOk(BaseModel):
    status: Literal["ok"] = "ok"
    backend: BackendName = "openvpn3"
    session_count: Annotated[int, Field(description="Number of live sessions.")]
    sessions: Annotated[list[SessionView], Field(description="One entry per live session.")]


class VpnConnectedOk(BaseModel):
    status: Literal["connected"] = "connected"
    backend: BackendName = "openvpn3"
    profile_name: Annotated[str, Field(description="Config name the tunnel is bound to.")]
    session: Annotated[SessionView, Field(description="Snapshot of the new session.")]
    overrides_applied: Annotated[
        dict[str, Any],
        Field(
            description="Effective overrides set on the config before NewTunnel (baseline + caller's, caller wins); "
            "keys the config already held with the same value aren't re-sent. Always empty on openvpn2, which takes none."
        ),
    ]
    notes: Annotated[
        list[str],
        Field(description="What this connect did that the caller should know (openvpn2: routes installed, DNS left alone, when the tunnel closes itself)."),
    ] = []


class VpnAlreadyConnected(BaseModel):
    status: Literal["already_connected"] = "already_connected"
    backend: BackendName = "openvpn3"
    profile_name: Annotated[str, Field(description="Config name bound to the existing session.")]
    session: Annotated[SessionView, Field(description="Snapshot of the existing session.")]


class VpnDisconnectedOk(BaseModel):
    status: Literal["disconnected"] = "disconnected"
    profile_name: Annotated[str, Field(description="Config name whose session was torn down.")]
    session_cleared: Annotated[
        bool,
        Field(description=f"True if teardown completed within {_DISCONNECT_TIMEOUT_SECS:g}s; False on timeout."),
    ]


class VpnNotConnected(BaseModel):
    status: Literal["not_connected"] = "not_connected"
    profile_name: Annotated[str, Field(description="Config name; no live session was registered under it.")]


class VpnError(BaseModel):
    status: Literal["error"] = "error"
    profile_name: Annotated[str | None, Field(description="Config name; null if failure preceded name resolution.")] = None
    message: Annotated[str, Field(description="Failure summary; multi-session disconnect errors are joined with '; '.")]


VpnStatusResult = Annotated[VpnStatusOk | VpnError, Field(discriminator="status")]
VpnConnectResult = Annotated[VpnConnectedOk | VpnAlreadyConnected | VpnError, Field(discriminator="status")]
VpnDisconnectResult = Annotated[VpnDisconnectedOk | VpnNotConnected | VpnError, Field(discriminator="status")]


def _ephemeral_profile_name(session_id: str) -> str:
    return f"ovpn3-od-{session_id}"


def _get_config_mgr() -> Any:
    return openvpn3.ConfigurationManager(dbus.SystemBus())


def _get_session_mgr() -> Any:
    return openvpn3.SessionManager(dbus.SystemBus())


def _dbus_error_msg(exc: BaseException) -> str:
    if isinstance(exc, dbus.exceptions.DBusException):
        return exc.get_dbus_message()
    return str(exc)


def _format_status(st: dict[str, Any]) -> str:
    major = getattr(st.get("major"), "name", "?")
    minor = getattr(st.get("minor"), "name", "?")
    message = str(st.get("message", "") or "")
    return f"{major}/{minor}" + (f": {message}" if message else "")


def _session_view(sess: Any) -> SessionView:
    config_name = "<unknown>"
    for key in ("config_name", "session_name"):
        with suppress(*_DBUS_ERRORS):
            config_name = str(sess.GetProperty(key))
            break
    try:
        status = _format_status(sess.GetStatus())
    except _DBUS_ERRORS as exc:
        status = f"<unavailable: {_dbus_error_msg(exc)}>"
    return SessionView(path=str(sess.GetPath()), config_name=config_name, status=status)


def _lookup(mgr: Any, profile: str) -> list:
    # Both managers answer "no match" with an empty list; an exception is a real failure (access denied, service
    # down) and propagates — reading it as "nothing there" would report not_connected / "Import it first" wrongly.
    return [mgr.Retrieve(p) for p in mgr.LookupConfigName(profile)]


def _sessions_for(profile: str) -> list:
    return _lookup(_get_session_mgr(), profile)


def _configs_for(profile: str) -> list:
    return _lookup(_get_config_mgr(), profile)


def _dbus_failure(profile_name: str | None, exc: BaseException) -> VpnError:
    return VpnError(profile_name=profile_name, message=f"D-Bus error: {_dbus_error_msg(exc)}")


def _cleanup_hint(profile_name: str, *, stale: bool = False) -> str:
    # `--disconnect` needs a live backend; `--cleanup` is openvpn3's sweep for sessions whose backend died.
    if stale:
        return "clear stale sessions with `openvpn3 session-manage --cleanup`, then retry"
    return f"disconnect it with `openvpn3 session-manage --disconnect --config {profile_name}`, then retry"


def _wait_session_cleared(profile: str, timeout: float = _DISCONNECT_TIMEOUT_SECS) -> bool:
    """Poll until `profile`'s session is gone or `timeout` hits; result feeds `VpnDisconnectedOk.session_cleared`.
    A failed lookup proves nothing, so it counts as "not cleared yet" rather than as an empty result."""
    deadline = time.monotonic() + timeout
    while True:
        with suppress(*_DBUS_ERRORS):
            if not _sessions_for(profile):
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _connect_failed(sess: Any, profile_name: str, message: str, *, teardown: bool, stale: bool = False) -> VpnError:
    if teardown:
        with suppress(*_DBUS_ERRORS):
            sess.Disconnect()
    else:
        message += f" The session predates this call, so it was left in place; {_cleanup_hint(profile_name, stale=stale)}."
    return VpnError(profile_name=profile_name, message=message)


def _await_connected(sess: Any, profile_name: str, *, teardown: bool = True) -> VpnError | None:
    """Poll status after `Connect()` until CONN_CONNECTED, terminal failure, vanish, or timeout.
    Async openvpn3 core rejections (e.g. UNUSED_OPTIONS_ERROR from a malformed `.ovpn`) would
    otherwise look like a transient `CONN_CONNECTING` and never surface. Failure tears the session
    down unless `teardown=False` (a session that predates this call — possibly the user's own)."""
    deadline = time.monotonic() + _CONNECT_TIMEOUT_SECS
    last_status = "<no status yet>"
    while time.monotonic() < deadline:
        try:
            st = sess.GetStatus()
        except _DBUS_ERRORS as exc:
            # Single-use configs whose tunnel-start fails are reaped with their session. If GetStatus only failed
            # transiently the session could still come up untracked, so the teardown runs anyway (no-op if gone).
            return _connect_failed(
                sess,
                profile_name,
                f"Connect failed — session vanished (last status: {last_status}; {_dbus_error_msg(exc)}).",
                teardown=teardown,
                stale=True,
            )
        last_status = _format_status(st)
        minor_name = getattr(st.get("minor"), "name", "?")
        if minor_name == "CONN_CONNECTED":
            return None
        if minor_name in _CONNECT_FAILURE_MINORS:
            return _connect_failed(sess, profile_name, f"Connect failed ({last_status}).", teardown=teardown)
        time.sleep(0.2)
    return _connect_failed(
        sess,
        profile_name,
        f"Connect did not reach CONN_CONNECTED within {_CONNECT_TIMEOUT_SECS:g}s; last status: {last_status}.",
        teardown=teardown,
    )


def _existing_session(profile_name: str) -> VpnAlreadyConnected | VpnError | None:
    """Idempotency guard: None when no session holds `profile_name`; `already_connected` only once it is really up.
    The session predates this call and may be the user's own, so a non-connected one is reported, never torn down."""
    existing = _sessions_for(profile_name)
    if not existing:
        return None
    sess = existing[0]
    try:
        st = sess.GetStatus()
    except _DBUS_ERRORS as exc:
        return VpnError(
            profile_name=profile_name,
            message=f"A session for {profile_name!r} exists but its backend isn't answering ({_dbus_error_msg(exc)}); {_cleanup_hint(profile_name, stale=True)}.",
        )
    minor_name = getattr(st.get("minor"), "name", "?")
    if minor_name in _CONNECT_PENDING_MINORS:
        if err := _await_connected(sess, profile_name, teardown=False):
            return err
    elif minor_name != "CONN_CONNECTED":
        return VpnError(
            profile_name=profile_name,
            message=f"A session for {profile_name!r} exists but isn't connected ({_format_status(st)}); {_cleanup_hint(profile_name)}.",
        )
    return VpnAlreadyConnected(profile_name=profile_name, session=_session_view(sess))


def _ovpn2_view(info: dict[str, str]) -> SessionView:
    status = info["state"].upper() + (f": {info['detail']}" if info.get("detail") else "")
    return SessionView(path=info["unit"], config_name=info["name"], status=status)


def _ovpn2_failure(profile_name: str | None, exc: BaseException) -> VpnError:
    return VpnError(profile_name=profile_name, message=f"OpenVPN 2 backend: {exc}")


def _ovpn2_status() -> VpnStatusOk | VpnError:
    try:
        views = [_ovpn2_view(info) for info in ovpn2.sessions()]
    except _OVPN2_ERRORS as exc:
        return _ovpn2_failure(None, exc)
    return VpnStatusOk(backend="openvpn2", session_count=len(views), sessions=views)


def _ovpn2_existing(profile_name: str) -> VpnAlreadyConnected | VpnError | None:
    """Same idempotency guard as `_existing_session`: None when nothing holds the name, `already_connected` only once the
    tunnel is really up, and a tunnel in any other state is reported, never torn down — it predates this call."""
    info = ovpn2.session(profile_name)
    if info is not None and info["state"] == "connecting":
        info = ovpn2.wait_connected(profile_name, _CONNECT_TIMEOUT_SECS)
    if info is None:
        return None
    if info["state"] == "connected":
        return VpnAlreadyConnected(backend="openvpn2", profile_name=profile_name, session=_ovpn2_view(info))
    return VpnError(
        profile_name=profile_name,
        message=f"A tunnel for {profile_name!r} exists but isn't connected ({_ovpn2_view(info).status}). "
        f"It predates this call, so it was left in place; {ovpn2.stop_hint(profile_name)}.",
    )


def _ovpn2_start(profile_name: str, profile_text: str | None, overrides: dict[str, Any] | None, *, looked_up: bool = False) -> VpnConnectedOk | VpnAlreadyConnected | VpnError:
    try:
        if not looked_up and (found := _ovpn2_existing(profile_name)) is not None:
            return found
    except _OVPN2_ERRORS as exc:
        return _ovpn2_failure(profile_name, exc)
    try:
        result = ovpn2.up(profile_name, profile_text)
    except _OVPN2_ERRORS as exc:
        return _ovpn2_failure(profile_name, exc)
    if result["status"] == "exists":
        # Another call got there first (two sessions sharing a profile name): treat it like any tunnel found under the name.
        try:
            if (found := _ovpn2_existing(profile_name)) is not None:
                return found
        except _OVPN2_ERRORS as exc:
            return _ovpn2_failure(profile_name, exc)
        return VpnError(profile_name=profile_name, message=f"A tunnel for {profile_name!r} was starting and is gone again; connect once more.")
    if result["status"] == "no_config":
        return VpnError(profile_name=profile_name, message=f"{result['message']} Import it first: `{ovpn2.import_hint(profile_name)}`.")
    if result["status"] != "connected":
        return VpnError(profile_name=profile_name, message=str(result.get("message") or f"unexpected helper answer {result['status']!r}"))
    view = SessionView(path=result["unit"], config_name=profile_name, status=f"CONNECTED: {ovpn2.helper.CONNECTED_STATUS}")
    notes = ovpn2.connect_notes(result)
    if overrides:
        # One settings file may serve an openvpn3 workstation and an OpenVPN 2 host; say what didn't apply instead of failing.
        notes.append(f"config_overrides not applied ({', '.join(sorted(overrides))}): they are openvpn3 settings and the OpenVPN 2 backend takes none.")
    return VpnConnectedOk(backend="openvpn2", profile_name=profile_name, session=view, overrides_applied={}, notes=notes)


def _ovpn2_disconnect(profile_name: str) -> VpnDisconnectedOk | VpnNotConnected | VpnError:
    try:
        if ovpn2.session(profile_name) is None:
            return VpnNotConnected(profile_name=profile_name)
        result = ovpn2.down(profile_name)
    except _OVPN2_ERRORS as exc:
        return _ovpn2_failure(profile_name, exc)
    if result["status"] == "not_connected":
        return VpnNotConnected(profile_name=profile_name)
    if result["status"] != "disconnected":
        return VpnError(profile_name=profile_name, message=str(result.get("message") or f"unexpected helper answer {result['status']!r}"))
    return VpnDisconnectedOk(profile_name=profile_name, session_cleared=bool(result.get("cleared")))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def vpn_status() -> VpnStatusResult:
    """List active VPN sessions with profile names and statuses, and say which backend (openvpn3 or openvpn2) this host uses."""
    if BACKEND == "openvpn2":
        return _ovpn2_status()
    try:
        sessions = _get_session_mgr().FetchAvailableSessions()
    except _DBUS_ERRORS as exc:
        return _dbus_failure(None, exc)
    views = [_session_view(s) for s in sessions]
    return VpnStatusOk(session_count=len(views), sessions=views)


def _wrap_override_value(value: Any) -> Any:
    # openvpn3's SetOverride only accepts bool/string variants (Int32 → "Unsupported override data type: i").
    # `bool()` must precede the catch-all because bool is an int subclass.
    match value:
        case bool():
            return dbus.Boolean(value)
        case _:
            return dbus.String(str(value))


_OVERRIDES_FIELD_DESCRIPTION = (
    "openvpn3 backend only (on openvpn2 they are not applied, and the result's `notes` says so). "
    "Optional {name: value} map of openvpn3 config-manage overrides set on the config before NewTunnel (e.g. "
    "'dns-scope', 'persist-tun', 'log-level'). Bools marshal as D-Bus bool; everything else stringifies (the "
    "daemon's SetOverride only accepts bool/string). Baseline `dns-scope=tunnel` is applied automatically; caller "
    "entries win. Skipped on `already_connected`. On a BYO profile these are written into the profile itself and "
    "persist (also for a manual `openvpn3 session-start`); dropping a key here does not unset it — that needs "
    "`openvpn3 config-manage --config <name> --unset-override <key>`."
)

_VPN_CONNECT_EPHEMERAL_DESCRIPTION = (
    "Import a fresh single-use config from a .ovpn file and start the session under "
    "`ovpn3-od-{session_id}`. Idempotent — returns `already_connected` (without re-importing) "
    "if that name's session is already up; an existing session that isn't up is reported as an "
    "error, not torn down. For BYO profiles use `vpn_connect`. On the openvpn2 backend the profile "
    "must pass an allowlist (no scripts, plugins, log files or routes of its own); a refusal names the line."
)


def _start_session(profile_name: str, overrides: dict[str, Any] | None) -> VpnConnectedOk | VpnAlreadyConnected | VpnError:
    try:
        if (found := _existing_session(profile_name)) is not None:
            return found
        configs = _configs_for(profile_name)
    except _DBUS_ERRORS as exc:
        return _dbus_failure(profile_name, exc)
    if not configs:
        return VpnError(profile_name=profile_name, message=f"No openvpn3 config named {profile_name!r}. Import it first.")
    effective_overrides = {**_DEFAULT_OVERRIDES, **(overrides or {})}
    # SetOverride rewrites a persistent (BYO) profile on disk, so skip keys it already holds with the same value.
    # Unreadable overrides just mean every key gets sent.
    current: dict[str, Any] = {}
    with suppress(*_DBUS_ERRORS):
        current = dict(configs[0].GetOverrides())
    for name, value in effective_overrides.items():
        wrapped = _wrap_override_value(value)
        if current.get(name) == wrapped:
            continue
        try:
            configs[0].SetOverride(name, wrapped)
        except _DBUS_ERRORS as exc:
            return VpnError(profile_name=profile_name, message=f"SetOverride {name!r} failed: {_dbus_error_msg(exc)}")
    try:
        sess = _get_session_mgr().NewTunnel(configs[0])
    except _DBUS_ERRORS as exc:
        return VpnError(profile_name=profile_name, message=f"NewTunnel failed: {_dbus_error_msg(exc)}")
    # Ready() raises while the backend is initialising or wants interactive credentials; we don't prompt.
    deadline = time.monotonic() + 15.0
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            sess.Ready()
            break
        except _DBUS_ERRORS as exc:
            last_err = exc
            time.sleep(0.2)
    else:
        with suppress(*_DBUS_ERRORS):
            sess.Disconnect()
        return VpnError(
            profile_name=profile_name,
            message=f"Backend not ready (likely needs credentials embedded in the profile): {_dbus_error_msg(last_err) if last_err else 'timeout'}",
        )
    try:
        sess.Connect()
    except _DBUS_ERRORS as exc:
        # NewTunnel registered the session under the config name; left alive it would read as `already_connected`.
        with suppress(*_DBUS_ERRORS):
            sess.Disconnect()
        return VpnError(profile_name=profile_name, message=f"Connect failed: {_dbus_error_msg(exc)}")
    if err := _await_connected(sess, profile_name):
        return err
    return VpnConnectedOk(
        profile_name=profile_name,
        session=_session_view(sess),
        overrides_applied=dict(effective_overrides),
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def vpn_connect(
    profile_name: Annotated[str, Field(description="Name of an already-imported OpenVPN3 config.")],
    overrides: Annotated[dict[str, Any] | None, Field(description=_OVERRIDES_FIELD_DESCRIPTION)] = None,
) -> VpnConnectResult:
    """Start a VPN session for a BYO profile (openvpn3: an imported config; openvpn2: /etc/openvpn/client/<name>.conf).
    Idempotent — `already_connected` only if the existing session is up; one that isn't is reported as an error, not torn
    down. For provisioned-each-turn profiles use `vpn_connect_ephemeral`."""
    if BACKEND == "openvpn2":
        return _ovpn2_start(profile_name, None, overrides)
    return _start_session(profile_name, overrides)


def _read_profile(ovpn_path: str, profile_name: str) -> str | VpnError:
    path = Path(ovpn_path).expanduser().resolve()
    if not path.is_file():
        return VpnError(profile_name=profile_name, message=f"File not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return VpnError(profile_name=profile_name, message=f"Cannot read {path}: {exc}")
    except UnicodeDecodeError as exc:
        # Import takes a D-Bus string (must be UTF-8); errors="replace" would silently mangle the profile.
        return VpnError(profile_name=profile_name, message=f"{path} is not valid UTF-8 ({exc}); re-encode it as UTF-8.")


@mcp.tool(
    description=_VPN_CONNECT_EPHEMERAL_DESCRIPTION,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def vpn_connect_ephemeral(
    ovpn_path: Annotated[
        str,
        Field(
            description=(
                "Path to a .ovpn file (~ expansion supported). Imported as single-use under "
                "`ovpn3-od-{session_id}`, then connected. Callers should write it to a private mode-600 "
                "file and `rm` it after the call so contents stay out of the conversation transcript."
            )
        ),
    ],
    session_id: Annotated[
        str,
        Field(
            description=(
                "Per-task tag for the ephemeral profile name (`ovpn3-od-{session_id}`). The caller supplies "
                "a session/thread identifier or a generated UUID; the server cannot infer the caller's identity."
            )
        ),
    ],
    overrides: Annotated[dict[str, Any] | None, Field(description=_OVERRIDES_FIELD_DESCRIPTION)] = None,
) -> VpnConnectResult:
    if not (session_id := session_id.strip()):
        return VpnError(message="session_id is required (pass the task identifier from the skill).")
    profile_name = _ephemeral_profile_name(session_id)

    if BACKEND == "openvpn2":
        # Looked up before the file is read, like openvpn3 below: an existing tunnel answers without needing the file.
        try:
            if (found := _ovpn2_existing(profile_name)) is not None:
                return found
        except _OVPN2_ERRORS as exc:
            return _ovpn2_failure(profile_name, exc)
        cfg_str = _read_profile(ovpn_path, profile_name)
        return cfg_str if isinstance(cfg_str, VpnError) else _ovpn2_start(profile_name, cfg_str, overrides, looked_up=True)

    try:
        if (found := _existing_session(profile_name)) is not None:
            return found
        # Clean up any stale config from a prior turn whose NewTunnel didn't consume it.
        # Failure must stop the import: a second config with this name could leave
        # _start_session selecting the stale profile instead of the fresh one.
        for cfg in _configs_for(profile_name):
            cfg.Remove()
    except _DBUS_ERRORS as exc:
        return _dbus_failure(profile_name, exc)

    cfg_str = _read_profile(ovpn_path, profile_name)
    if isinstance(cfg_str, VpnError):
        return cfg_str
    # Raw contents go to ConfigurationManager.Import — openvpn3's C++ parser is authoritative.
    # DO NOT pre-parse with openvpn3.ConfigParser: argparse-backed whitelist rejected valid directives in 0.4.0.
    try:
        _get_config_mgr().Import(profile_name, cfg_str, single_use=True, persistent=False)
    except _DBUS_ERRORS as exc:
        return VpnError(profile_name=profile_name, message=f"Import failed: {_dbus_error_msg(exc)}")

    result = _start_session(profile_name, overrides)
    if isinstance(result, VpnError):
        # NewTunnel consumes a single-use config; if we failed before that, drop it so the name is reusable.
        # Best effort — a lookup failure here must not replace the error being reported.
        with suppress(*_DBUS_ERRORS):
            for cfg in _configs_for(profile_name):
                with suppress(*_DBUS_ERRORS):
                    cfg.Remove()
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def vpn_disconnect(
    profile_name: Annotated[str, Field(description="Config name whose session should be torn down.")],
) -> VpnDisconnectResult:
    """Disconnect the VPN session for the given profile. No-op if not connected. `session_cleared` in the response reports whether teardown finished in time."""
    if not profile_name:
        return VpnError(message="profile_name is required")
    if BACKEND == "openvpn2":
        return _ovpn2_disconnect(profile_name)
    try:
        matches = _sessions_for(profile_name)
    except _DBUS_ERRORS as exc:
        return _dbus_failure(profile_name, exc)
    if not matches:
        return VpnNotConnected(profile_name=profile_name)
    failures: list[str] = []
    for sess in matches:
        try:
            sess.Disconnect()
        except _DBUS_ERRORS as exc:
            failures.append(_dbus_error_msg(exc))
    if failures:
        return VpnError(profile_name=profile_name, message="; ".join(failures))
    cleared = _wait_session_cleared(profile_name)
    return VpnDisconnectedOk(profile_name=profile_name, session_cleared=cleared)


def main() -> None:
    # AGPL-3.0 §5(d) notice; stdout is reserved for MCP stdio.
    print(f"openvpn3-mcp {__version__} — AGPL-3.0-only — backend: {BACKEND}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
