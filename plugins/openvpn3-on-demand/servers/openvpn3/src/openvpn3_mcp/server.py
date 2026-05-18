# SPDX-License-Identifier: AGPL-3.0-only
#
# Copyright (C) 2026 aeresov
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 as published
# by the Free Software Foundation. See the LICENSE file at the repo root.

"""openvpn3 MCP server — talks to openvpn3-linux over D-Bus via the ``openvpn3`` Python module."""

from __future__ import annotations

import sys
import time
from contextlib import suppress
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

__version__ = _pkg_version("openvpn3-mcp")

try:
    import dbus  # type: ignore[import-not-found]
    import openvpn3  # type: ignore[import-not-found]
except ImportError:
    print(
        "openvpn3-mcp: cannot import 'dbus' and/or 'openvpn3'. Install the 'python3-dbus' and 'openvpn3-client' system packages.",
        file=sys.stderr,
    )
    sys.exit(1)


mcp = FastMCP("openvpn3")

# Split-DNS baseline so the tunnel doesn't fight Tailscale / mDNS / other VPNs. Caller wins on key collision.
_DEFAULT_OVERRIDES: dict[str, Any] = {"dns-scope": "tunnel"}

# openvpn3 failure surface: D-Bus method errors + RuntimeError from manager __ping().
_DBUS_ERRORS = (dbus.exceptions.DBusException, RuntimeError)

# How long vpn_disconnect waits for the session to clear before reporting `session_cleared=False`.
_DISCONNECT_TIMEOUT_SECS: float = 5.0

# How long we poll session status after `Connect()` waiting for CONN_CONNECTED before giving up.
_CONNECT_TIMEOUT_SECS: float = 10.0

# StatusMinor names that mean the tunnel won't come up — fail fast instead of waiting for the timeout.
_CONNECT_FAILURE_MINORS: frozenset[str] = frozenset({"CONN_FAILED", "CONN_AUTH_FAILED", "CONN_DISCONNECTED"})


# Tagged-union result models, discriminated on `status`; FastMCP emits each tool's union as its outputSchema.


class SessionView(BaseModel):
    path: Annotated[str, Field(description="D-Bus object path.")]
    config_name: Annotated[str, Field(description="Registered openvpn3 config name.")]
    status: Annotated[str, Field(description="'MAJOR / MINOR: message' from the session manager.")]


class VpnStatusOk(BaseModel):
    status: Literal["ok"] = "ok"
    session_count: Annotated[int, Field(description="Number of live sessions.")]
    sessions: Annotated[list[SessionView], Field(description="One entry per live session.")]


class VpnConnectedOk(BaseModel):
    status: Literal["connected"] = "connected"
    profile_name: Annotated[str, Field(description="Config name the tunnel is bound to.")]
    session: Annotated[SessionView, Field(description="Snapshot of the new session.")]
    overrides_applied: Annotated[
        dict[str, Any],
        Field(description="Effective overrides pushed via SetOverride before NewTunnel (baseline + caller's, caller wins)."),
    ]


class VpnAlreadyConnected(BaseModel):
    status: Literal["already_connected"] = "already_connected"
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


def _session_view(sess: Any) -> SessionView:
    config_name = "<unknown>"
    for key in ("config_name", "session_name"):
        with suppress(*_DBUS_ERRORS):
            config_name = str(sess.GetProperty(key))
            break
    try:
        st = sess.GetStatus()
        status = f"{getattr(st.get('major'), 'name', '?')} / {getattr(st.get('minor'), 'name', '?')}: {st.get('message', '')}"
    except _DBUS_ERRORS as exc:
        status = f"<unavailable: {_dbus_error_msg(exc)}>"
    return SessionView(path=str(sess.GetPath()), config_name=config_name, status=status)


def _lookup(mgr: Any, profile: str) -> list:
    try:
        paths = mgr.LookupConfigName(profile)
    except _DBUS_ERRORS:
        return []
    return [mgr.Retrieve(p) for p in paths]


def _sessions_for(profile: str) -> list:
    return _lookup(_get_session_mgr(), profile)


def _configs_for(profile: str) -> list:
    return _lookup(_get_config_mgr(), profile)


def _wait_session_cleared(profile: str, timeout: float = _DISCONNECT_TIMEOUT_SECS) -> bool:
    """Poll until `profile`'s session is gone or `timeout` hits; result feeds `VpnDisconnectedOk.session_cleared`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _sessions_for(profile):
            return True
        time.sleep(0.1)
    return not _sessions_for(profile)


def _format_status(st: dict[str, Any]) -> str:
    major = getattr(st.get("major"), "name", "?")
    minor = getattr(st.get("minor"), "name", "?")
    message = str(st.get("message", "") or "")
    return f"{major}/{minor}" + (f": {message}" if message else "")


def _await_connected(sess: Any, profile_name: str) -> VpnError | None:
    """Poll session status after `Connect()` until CONN_CONNECTED, a terminal failure, the session vanishes,
    or `_CONNECT_TIMEOUT_SECS` elapses. Without this, openvpn3 core rejections (e.g. UNUSED_OPTIONS_ERROR
    from a malformed `.ovpn`) surface as a transient `CONN_CONNECTING` snapshot and the caller never learns
    the tunnel actually died."""
    deadline = time.monotonic() + _CONNECT_TIMEOUT_SECS
    last_status = "<no status yet>"
    while time.monotonic() < deadline:
        try:
            st = sess.GetStatus()
        except _DBUS_ERRORS as exc:
            # Single-use configs whose tunnel-start fails get reaped, taking the session with them.
            return VpnError(
                profile_name=profile_name,
                message=f"Connect failed — session vanished (last status: {last_status}; {_dbus_error_msg(exc)}).",
            )
        last_status = _format_status(st)
        minor_name = getattr(st.get("minor"), "name", "?")
        if minor_name == "CONN_CONNECTED":
            return None
        if minor_name in _CONNECT_FAILURE_MINORS:
            with suppress(*_DBUS_ERRORS):
                sess.Disconnect()
            return VpnError(profile_name=profile_name, message=f"Connect failed ({last_status}).")
        time.sleep(0.2)
    with suppress(*_DBUS_ERRORS):
        sess.Disconnect()
    return VpnError(
        profile_name=profile_name,
        message=f"Connect did not reach CONN_CONNECTED within {_CONNECT_TIMEOUT_SECS:g}s; last status: {last_status}.",
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def vpn_status() -> VpnStatusResult:
    """List active OpenVPN3 sessions with config names and statuses."""
    try:
        sessions = _get_session_mgr().FetchAvailableSessions()
    except _DBUS_ERRORS as exc:
        return VpnError(message=f"D-Bus error: {_dbus_error_msg(exc)}")
    views = [_session_view(s) for s in sessions]
    return VpnStatusOk(session_count=len(views), sessions=views)


def _wrap_override_value(value: Any) -> Any:
    # openvpn3's SetOverride only accepts bool/string variants — Int32 ('i') gets rejected with
    # "Unsupported override data type: i". Bools take dbus.Boolean (e.g. `persist-tun`); everything else
    # (including ints like `log-level`) is stringified. `bool()` must precede the catch-all because
    # bool is an int subclass.
    match value:
        case bool():
            return dbus.Boolean(value)
        case _:
            return dbus.String(str(value))


_OVERRIDES_FIELD_DESCRIPTION = (
    "Optional {name: value} map of openvpn3 config-manage overrides applied before NewTunnel (e.g. 'dns-scope', "
    "'persist-tun', 'log-level'). Bools marshal as D-Bus bool; everything else (including ints) is stringified — "
    "the openvpn3 daemon's SetOverride only accepts bool/string variants. Server baseline `dns-scope=tunnel` "
    "is always applied; entries here override on collision. Skipped on `already_connected`."
)

_VPN_CONNECT_EPHEMERAL_DESCRIPTION = (
    "Import a fresh single-use config from a .ovpn file and start the session under "
    "`ovpn3-od-$CLAUDE_CODE_SESSION_ID`. Idempotent — returns `already_connected` "
    "(without re-importing) if that name's session is already up. For BYO profiles use `vpn_connect`."
)


def _start_session(profile_name: str, overrides: dict[str, Any] | None) -> VpnConnectedOk | VpnAlreadyConnected | VpnError:
    if existing := _sessions_for(profile_name):
        return VpnAlreadyConnected(profile_name=profile_name, session=_session_view(existing[0]))
    configs = _configs_for(profile_name)
    if not configs:
        return VpnError(profile_name=profile_name, message=f"No openvpn3 config named {profile_name!r}. Import it first.")
    effective_overrides = {**_DEFAULT_OVERRIDES, **(overrides or {})}
    for name, value in effective_overrides.items():
        try:
            configs[0].SetOverride(name, _wrap_override_value(value))
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
    """Start an OpenVPN3 session for a BYO profile. Idempotent. For provisioned-each-turn profiles use `vpn_connect_ephemeral`."""
    return _start_session(profile_name, overrides)


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
                "`ovpn3-od-{session_id}`, then connected. Callers should write it via `mktemp` "
                "and `rm` after the call so contents stay out of the conversation transcript."
            )
        ),
    ],
    session_id: Annotated[
        str,
        Field(
            description=(
                "Per-task tag used to derive the ephemeral profile name (`ovpn3-od-{session_id}`). The skill reads "
                "`$CLAUDE_CODE_SESSION_ID` and forwards it; the MCP server can't read that env var itself because "
                "Claude Code doesn't propagate it to MCP subprocesses (the server is a singleton across "
                "`/resume`/`/fork-session`)."
            )
        ),
    ],
    overrides: Annotated[dict[str, Any] | None, Field(description=_OVERRIDES_FIELD_DESCRIPTION)] = None,
) -> VpnConnectResult:
    if not (session_id := session_id.strip()):
        return VpnError(message="session_id is required (pass $CLAUDE_CODE_SESSION_ID from the skill).")
    profile_name = _ephemeral_profile_name(session_id)

    if existing := _sessions_for(profile_name):
        return VpnAlreadyConnected(profile_name=profile_name, session=_session_view(existing[0]))

    # Clean up any stale config from a prior turn whose NewTunnel didn't consume it.
    for cfg in _configs_for(profile_name):
        with suppress(*_DBUS_ERRORS):
            cfg.Remove()

    path = Path(ovpn_path).expanduser().resolve()
    if not path.is_file():
        return VpnError(profile_name=profile_name, message=f"File not found: {path}")
    try:
        cfg_str = path.read_text(encoding="utf-8")
    except OSError as exc:
        return VpnError(profile_name=profile_name, message=f"Cannot read {path}: {exc}")
    # Raw contents go to ConfigurationManager.Import — openvpn3's C++ parser is authoritative.
    # DO NOT pre-parse with openvpn3.ConfigParser: argparse-backed whitelist rejected valid directives in 0.4.0.
    try:
        _get_config_mgr().Import(profile_name, cfg_str, single_use=True, persistent=False)
    except _DBUS_ERRORS as exc:
        return VpnError(profile_name=profile_name, message=f"Import failed: {_dbus_error_msg(exc)}")

    return _start_session(profile_name, overrides)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def vpn_disconnect(
    profile_name: Annotated[str, Field(description="Config name whose session should be torn down.")],
) -> VpnDisconnectResult:
    """Disconnect the OpenVPN3 session for the given profile. No-op if not connected. `session_cleared` in the response reports whether teardown finished in time."""
    if not profile_name:
        return VpnError(message="profile_name is required")
    matches = _sessions_for(profile_name)
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
    print(f"openvpn3-mcp {__version__} — AGPL-3.0-only", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
