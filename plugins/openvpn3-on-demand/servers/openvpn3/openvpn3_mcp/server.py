# SPDX-License-Identifier: AGPL-3.0-only
#
# Copyright (C) 2026 aeresov
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 as published
# by the Free Software Foundation. See the LICENSE file at the repo root.

"""openvpn3 MCP server.

Talks to openvpn3-linux over its D-Bus services
(``net.openvpn.v3.configuration`` and ``net.openvpn.v3.sessions``) using the
``openvpn3`` Python module shipped with the ``openvpn3-client`` system
package. No CLI shell-out, no stdout parsing — dict returns mirror what the
previous subprocess-based server exposed so the skill keeps working.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

try:
    import dbus  # type: ignore[import-not-found]
    import openvpn3  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - exercised only via _require_deps
    _IMPORT_ERROR: Optional[ImportError] = exc
    dbus = None  # type: ignore[assignment]
    openvpn3 = None  # type: ignore[assignment]
else:
    _IMPORT_ERROR = None


mcp = FastMCP("openvpn3")


_DEP_ERROR = {
    "status": "error",
    "message": (
        "openvpn3 Python module or dbus-python is not available. "
        "Install the 'openvpn3-client' and 'python3-dbus' system packages."
    ),
}


def _require_deps() -> Optional[dict]:
    return None if _IMPORT_ERROR is None else _DEP_ERROR


def _bus() -> Any:
    return dbus.SystemBus()


def _get_config_mgr() -> Any:
    return openvpn3.ConfigurationManager(_bus())


def _get_session_mgr() -> Any:
    return openvpn3.SessionManager(_bus())


def _dbus_error_msg(exc: BaseException) -> str:
    """Normalize D-Bus / openvpn3 errors into a single human string.

    ``ConfigurationManager`` / ``SessionManager`` both raise ``RuntimeError``
    from their private ``__ping()`` helper when the D-Bus service is
    unreachable or access is denied; actual method calls raise
    ``dbus.exceptions.DBusException``. Callers catch both, then ask this
    helper for a message they can hand back to the MCP client.
    """
    if isinstance(exc, dbus.exceptions.DBusException):
        return exc.get_dbus_message()
    return str(exc)


def _session_config_name(sess: Any) -> str:
    try:
        return str(sess.GetProperty("config_name"))
    except (dbus.exceptions.DBusException, RuntimeError):
        try:
            return str(sess.GetProperty("session_name"))
        except (dbus.exceptions.DBusException, RuntimeError):
            return "<unknown>"


def _session_status(sess: Any) -> str:
    try:
        status = sess.GetStatus()
    except (dbus.exceptions.DBusException, RuntimeError) as exc:
        return f"<unavailable: {_dbus_error_msg(exc)}>"
    major = getattr(status.get("major"), "name", "?")
    minor = getattr(status.get("minor"), "name", "?")
    message = status.get("message", "")
    return f"{major} / {minor}: {message}"


def _session_as_dict(sess: Any) -> dict:
    return {
        "path": str(sess.GetPath()),
        "config_name": _session_config_name(sess),
        "status": _session_status(sess),
    }


def _sessions_for(profile: str) -> list:
    """Return live Session objects whose registered config name matches ``profile``."""
    mgr = _get_session_mgr()
    try:
        paths = mgr.LookupConfigName(profile)
    except (dbus.exceptions.DBusException, RuntimeError):
        return []
    return [mgr.Retrieve(p) for p in paths]


def _configs_for(profile: str) -> list:
    """Return Configuration objects registered under ``profile`` (may be many)."""
    mgr = _get_config_mgr()
    try:
        paths = mgr.LookupConfigName(profile)
    except (dbus.exceptions.DBusException, RuntimeError):
        return []
    return [mgr.Retrieve(p) for p in paths]


def _wait_session_cleared(profile: str, timeout: float = 5.0) -> bool:
    """Poll ``_sessions_for`` until ``profile``'s session is gone or timeout hits.

    ``Session.Disconnect()`` asks the session-manager to tear down the tunnel;
    in practice the session-manager's own cleanup (netcfg release, D-Bus
    object removal) can trail by a beat. A follow-up ``vpn_config_remove``
    issued inside that window hits "config in use" because the session is
    still attached to the config. Callers of ``vpn_disconnect`` expect the
    session to be gone on return — this polls at 100 ms intervals until it
    actually is, giving up after ``timeout``. Returns True if cleared.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _sessions_for(profile):
            return True
        time.sleep(0.1)
    return not _sessions_for(profile)




@mcp.tool()
def vpn_status() -> dict:
    """List active OpenVPN3 sessions with config names and statuses. No arguments."""
    err = _require_deps()
    if err:
        return err
    try:
        sessions = _get_session_mgr().FetchAvailableSessions()
    except (dbus.exceptions.DBusException, RuntimeError) as exc:
        return {"status": "error", "message": f"D-Bus error: {_dbus_error_msg(exc)}"}
    out = [_session_as_dict(s) for s in sessions]
    return {"session_count": len(out), "sessions": out}


@mcp.tool()
def vpn_connect(profile_name: str) -> dict:
    """Start an OpenVPN3 session for the given imported profile. Idempotent: returns early if already connected.

    Args:
        profile_name: Name of a previously-imported OpenVPN3 config (the name passed to `vpn_config_import`).
    """
    err = _require_deps()
    if err:
        return err
    existing = _sessions_for(profile_name)
    if existing:
        return {
            "status": "already_connected",
            "profile_name": profile_name,
            "session": _session_as_dict(existing[0]),
        }
    configs = _configs_for(profile_name)
    if not configs:
        return {
            "status": "error",
            "profile_name": profile_name,
            "message": f"No openvpn3 config named {profile_name!r}. Import it first.",
        }
    try:
        sess = _get_session_mgr().NewTunnel(configs[0])
    except (dbus.exceptions.DBusException, RuntimeError) as exc:
        return {
            "status": "error",
            "profile_name": profile_name,
            "message": f"NewTunnel failed: {_dbus_error_msg(exc)}",
        }
    # Poll Ready() with a short deadline. It raises while the backend is still
    # initialising or if interactive credentials are required; non-interactive
    # profiles reach ready within a few D-Bus round-trips. We don't handle
    # user-input prompts — those need to be baked into the .ovpn (embedded
    # auth-user-pass) for this non-interactive server.
    deadline = time.monotonic() + 15.0
    last_err: Optional[BaseException] = None
    while time.monotonic() < deadline:
        try:
            sess.Ready()
            break
        except (dbus.exceptions.DBusException, RuntimeError) as exc:
            last_err = exc
            time.sleep(0.2)
    else:
        try:
            sess.Disconnect()
        except (dbus.exceptions.DBusException, RuntimeError):
            pass
        return {
            "status": "error",
            "profile_name": profile_name,
            "message": (
                "Backend not ready (likely needs credentials embedded in the profile): "
                f"{_dbus_error_msg(last_err) if last_err else 'timeout'}"
            ),
        }
    try:
        sess.Connect()
    except (dbus.exceptions.DBusException, RuntimeError) as exc:
        return {
            "status": "error",
            "profile_name": profile_name,
            "message": f"Connect failed: {_dbus_error_msg(exc)}",
        }
    return {
        "status": "connected",
        "profile_name": profile_name,
        "session": _session_as_dict(sess),
    }


@mcp.tool()
def vpn_disconnect(profile_name: str) -> dict:
    """Disconnect the OpenVPN3 session for the given profile. No-op if not connected.

    Waits (up to 5s) for the session-manager's teardown to actually complete
    before returning, so a follow-up ``vpn_config_remove`` or re-import
    doesn't hit "config in use" races. The returned payload includes
    ``session_cleared`` (bool) so callers can detect the rare case where
    teardown didn't finish in time.

    Args:
        profile_name: Name of the config whose session should be torn down. Required — this tool
            never disconnects sessions it wasn't asked about.
    """
    err = _require_deps()
    if err:
        return err
    if not profile_name:
        return {"status": "error", "message": "profile_name is required"}
    matches = _sessions_for(profile_name)
    if not matches:
        return {"status": "not_connected", "profile_name": profile_name}
    failures: list[str] = []
    for sess in matches:
        try:
            sess.Disconnect()
        except (dbus.exceptions.DBusException, RuntimeError) as exc:
            failures.append(_dbus_error_msg(exc))
    if failures:
        return {
            "status": "error",
            "profile_name": profile_name,
            "failures": failures,
        }
    cleared = _wait_session_cleared(profile_name)
    return {
        "status": "disconnected",
        "profile_name": profile_name,
        "session_cleared": cleared,
    }


@mcp.tool()
def vpn_config_import(
    ovpn_path: str, profile_name: str, single_use: bool = False
) -> dict:
    """Import a .ovpn file as a named OpenVPN3 config. Idempotent: returns early if a config with this name already exists.

    Note: ``single_use`` has no effect if a config with this name already exists — the
    existing config is returned as-is (``status: already_imported``).

    Args:
        ovpn_path: Path to the .ovpn file to import (~ expansion supported).
        profile_name: Name to register the imported config under.
        single_use: If True, register an ephemeral config — memory-only (not written to
            openvpn3's on-disk config store) and dropped by openvpn3 once a tunnel is started
            from it. Use for throwaway profiles. Default False (persistent, like the openvpn3
            `config-import --persistent` CLI does).
    """
    err = _require_deps()
    if err:
        return err
    path = Path(os.path.expanduser(ovpn_path)).resolve()
    if not path.is_file():
        return {"status": "error", "message": f"File not found: {path}"}
    if _configs_for(profile_name):
        return {"status": "already_imported", "profile_name": profile_name}
    try:
        cfg_str = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "status": "error",
            "profile_name": profile_name,
            "message": f"Cannot read {path}: {exc}",
        }
    # Hand the raw .ovpn contents to the configuration manager — openvpn3's
    # own C++ parser (net.openvpn.v3.configuration.Import) is authoritative
    # and accepts every directive the openvpn3 CLI accepts. DO NOT pre-parse
    # with openvpn3.ConfigParser here: that class is argparse-backed and
    # rejects any directive not in its whitelist (e.g. AWS Client VPN's
    # --remote-random-hostname), which is why 0.4.0 regressed importing
    # real-world profiles.
    #
    # The contract for Import per ConfigManager.py is that external file
    # references must be pre-inlined. Every major provider (AWS Client VPN,
    # OpenVPN Access Server, Tunnelblick, network-manager-openvpn exports)
    # already emits inlined <ca>/<cert>/<key>/<tls-crypt> blocks; if a user
    # hands us a profile with bare `ca /path/to/file` instead, the backend
    # raises a DBus error and we surface it verbatim.
    try:
        cfg = _get_config_mgr().Import(
            profile_name,
            cfg_str,
            single_use,         # single_use
            not single_use,     # persistent — ephemeral configs aren't written to disk
        )
    except (dbus.exceptions.DBusException, RuntimeError) as exc:
        return {
            "status": "error",
            "profile_name": profile_name,
            "message": f"Import failed: {_dbus_error_msg(exc)}",
        }
    return {
        "status": "imported",
        "profile_name": profile_name,
        "ovpn_path": str(path),
        "config_path": str(cfg.GetPath()),
        "single_use": single_use,
    }


@mcp.tool()
def vpn_config_remove(profile_name: str) -> dict:
    """Remove every OpenVPN3 config registered under this name. Idempotent.

    The configuration manager refuses to remove a config whose session is
    still active; call `vpn_disconnect` first.

    Args:
        profile_name: Name of the imported config to remove.
    """
    err = _require_deps()
    if err:
        return err
    matches = _configs_for(profile_name)
    if not matches:
        return {"status": "already_removed", "profile_name": profile_name}
    failures: list[str] = []
    removed = 0
    for cfg in matches:
        try:
            cfg.Remove()
            removed += 1
        except (dbus.exceptions.DBusException, RuntimeError) as exc:
            failures.append(_dbus_error_msg(exc))
    if failures:
        return {
            "status": "error",
            "profile_name": profile_name,
            "removed_count": removed,
            "failures": failures,
        }
    return {"status": "removed", "profile_name": profile_name, "removed_count": removed}


def main() -> None:
    # AGPL-3.0 §5(d): make the copyright notice visible. The MCP stdio
    # protocol reserves stdout, so the banner goes to stderr.
    print("openvpn3-mcp 0.6.0 — AGPL-3.0-only", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
