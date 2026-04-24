"""
openvpn3 MCP server.

Exposes five tools that shell out to the openvpn3 CLI:
  - vpn_status()
  - vpn_connect(profile_name)
  - vpn_disconnect(profile_name)
  - vpn_config_import(ovpn_path, profile_name)
  - vpn_config_remove(profile_name)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openvpn3")

OPENVPN3 = "openvpn3"


def _run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _require_cli() -> Optional[dict]:
    if not shutil.which(OPENVPN3):
        return {
            "status": "error",
            "message": f"{OPENVPN3} CLI not found on PATH. Install openvpn3-linux or equivalent.",
        }
    return None


def _parse_sessions(stdout: str) -> list[dict]:
    sessions: list[dict] = []
    current: dict = {}
    for raw in stdout.splitlines():
        if not raw.strip() or set(raw.strip()) == {"-"}:
            if current:
                sessions.append(current)
                current = {}
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            k = key.strip().lower().replace(" ", "_")
            current[k] = value.strip()
    if current:
        sessions.append(current)
    return sessions


def _list_sessions() -> list[dict]:
    r = _run(OPENVPN3, "sessions-list")
    if r.returncode != 0:
        return []
    return _parse_sessions(r.stdout)


def _find_session(profile: str) -> Optional[dict]:
    # Returns the first session whose config name matches. openvpn3 only allows
    # one active session per (config, user), but if duplicate-name configs ever
    # accumulated, this would only see the first one — `vpn_config_remove`
    # cleans those up by D-Bus path.
    for s in _list_sessions():
        if s.get("config_name") == profile or s.get("config") == profile:
            return s
    return None


def _wait_session_cleared(profile: str, timeout: float = 5.0) -> bool:
    """Poll sessions-list until `profile`'s session is gone or timeout elapses.

    `openvpn3 session-manage --disconnect` returns immediately, but the D-Bus
    teardown can take a beat. A follow-up `vpn_config_remove` issued within
    that window hits "config in use" because the session is still attached.
    Callers of `vpn_disconnect` expect the session to be gone on return; this
    polls at 100 ms intervals until it actually is, giving up after `timeout`.

    Returns True if the session cleared in time, False otherwise.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _find_session(profile) is None:
            return True
        time.sleep(0.1)
    return _find_session(profile) is None


def _all_configs() -> list[dict[str, str]]:
    """Return [{path, name}, ...] for every registered openvpn3 config.

    Tries `configs-list --json` first — newer openvpn3 builds emit structured
    output that's immune to formatting drift. Falls back to a whitespace-tolerant
    text parse (keyed on `Configuration path:` / `Name:` pairs separated by
    dashed divider rows) for older builds. Only entries with a non-empty path
    are returned; `name` may be the empty string for anonymous configs.
    """
    jr = _run(OPENVPN3, "configs-list", "--json")
    if jr.returncode == 0 and jr.stdout.strip():
        try:
            data = json.loads(jr.stdout)
        except json.JSONDecodeError:
            data = None
        items: list[dict] = []
        if isinstance(data, list):
            items = [d for d in data if isinstance(d, dict)]
        elif isinstance(data, dict):
            items = [d for d in data.values() if isinstance(d, dict)]
        if items:
            out: list[dict[str, str]] = []
            for item in items:
                path = str(
                    item.get("path")
                    or item.get("dbus_path")
                    or item.get("config_path")
                    or ""
                )
                name = str(item.get("name") or item.get("config_name") or "")
                if path:
                    out.append({"path": path, "name": name})
            return out

    r = _run(OPENVPN3, "configs-list")
    if r.returncode != 0:
        return []
    configs: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw in r.stdout.splitlines():
        stripped = raw.strip()
        if not stripped or set(stripped) <= {"-"}:
            if current.get("path"):
                configs.append({"path": current["path"], "name": current.get("name", "")})
            current = {}
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        k = key.strip().lower()
        v = value.strip()
        if k in {"configuration path", "path"}:
            if current.get("path"):
                configs.append({"path": current["path"], "name": current.get("name", "")})
            current = {"path": v}
        elif k == "name":
            current["name"] = v
    if current.get("path"):
        configs.append({"path": current["path"], "name": current.get("name", "")})
    return configs


def _list_configs() -> list[str]:
    return [c["name"] for c in _all_configs() if c["name"]]


@mcp.tool()
def vpn_status() -> dict:
    """List active OpenVPN3 sessions with config names and statuses. No arguments."""
    err = _require_cli()
    if err:
        return err
    sessions = _list_sessions()
    return {"session_count": len(sessions), "sessions": sessions}


@mcp.tool()
def vpn_connect(profile_name: str) -> dict:
    """Start an OpenVPN3 session for the given imported profile. Idempotent: returns early if already connected.

    Args:
        profile_name: Name of a previously-imported OpenVPN3 config (as shown by `openvpn3 configs-list`).
    """
    err = _require_cli()
    if err:
        return err
    existing = _find_session(profile_name)
    if existing:
        return {"status": "already_connected", "profile_name": profile_name, "session": existing}
    r = _run(OPENVPN3, "session-start", "--config", profile_name, timeout=60.0)
    if r.returncode != 0:
        return {
            "status": "error",
            "profile_name": profile_name,
            "returncode": r.returncode,
            "stderr": r.stderr.strip(),
            "stdout": r.stdout.strip(),
        }
    return {"status": "connected", "profile_name": profile_name, "output": r.stdout.strip()}


@mcp.tool()
def vpn_disconnect(profile_name: str) -> dict:
    """Disconnect the OpenVPN3 session for the given profile. No-op if not connected.

    Waits (up to 5s) for openvpn3's D-Bus session teardown to actually complete
    before returning, so a follow-up `vpn_config_remove` or re-import doesn't
    hit "config in use" races. The returned payload includes `session_cleared`
    (bool) so callers can detect the rare case where teardown didn't finish in
    time.

    Args:
        profile_name: Name of the config whose session should be torn down. Required — this tool
            never disconnects sessions it wasn't asked about.
    """
    err = _require_cli()
    if err:
        return err
    if not profile_name:
        return {"status": "error", "message": "profile_name is required"}
    target = _find_session(profile_name)
    if not target:
        return {"status": "not_connected", "profile_name": profile_name}
    r = _run(OPENVPN3, "session-manage", "--config", profile_name, "--disconnect", timeout=30.0)
    if r.returncode != 0:
        return {
            "status": "error",
            "profile_name": profile_name,
            "returncode": r.returncode,
            "stderr": r.stderr.strip(),
            "stdout": r.stdout.strip(),
        }
    cleared = _wait_session_cleared(profile_name)
    return {
        "status": "disconnected",
        "profile_name": profile_name,
        "session_cleared": cleared,
    }


@mcp.tool()
def vpn_config_import(ovpn_path: str, profile_name: str) -> dict:
    """Import a .ovpn file as a persistent OpenVPN3 config. Idempotent: returns early if a config with this name already exists.

    Args:
        ovpn_path: Path to the .ovpn file to import (~ expansion supported).
        profile_name: Name to register the imported config under.
    """
    err = _require_cli()
    if err:
        return err
    path = Path(os.path.expanduser(ovpn_path)).resolve()
    if not path.is_file():
        return {"status": "error", "message": f"File not found: {path}"}
    if profile_name in _list_configs():
        return {"status": "already_imported", "profile_name": profile_name}
    r = _run(
        OPENVPN3,
        "config-import",
        "--config",
        str(path),
        "--name",
        profile_name,
        "--persistent",
        timeout=30.0,
    )
    if r.returncode != 0:
        return {
            "status": "error",
            "profile_name": profile_name,
            "returncode": r.returncode,
            "stderr": r.stderr.strip(),
            "stdout": r.stdout.strip(),
        }
    return {"status": "imported", "profile_name": profile_name, "ovpn_path": str(path)}


@mcp.tool()
def vpn_config_remove(profile_name: str) -> dict:
    """Remove every OpenVPN3 config registered under this name. Idempotent.

    Targets each match by D-Bus path (`openvpn3 config-remove --path …`) so it
    handles the duplicate-name case cleanly: if two or more configs share the
    same name — which can happen when a prior import ran while a stale one
    was still registered — each is removed individually rather than hitting
    `config-remove --config <name>`'s "More than one configuration profile was
    found" error.

    openvpn3 refuses to remove a config whose session is still active; call
    `vpn_disconnect` first.

    Args:
        profile_name: Name of the imported config to remove.
    """
    err = _require_cli()
    if err:
        return err
    matches = [c["path"] for c in _all_configs() if c["name"] == profile_name]
    if not matches:
        return {"status": "already_removed", "profile_name": profile_name}
    failures: list[dict] = []
    removed = 0
    for path in matches:
        r = _run(OPENVPN3, "config-remove", "--path", path, "--force", timeout=15.0)
        if r.returncode == 0:
            removed += 1
        else:
            failures.append(
                {
                    "path": path,
                    "returncode": r.returncode,
                    "stderr": r.stderr.strip(),
                    "stdout": r.stdout.strip(),
                }
            )
    if failures:
        return {
            "status": "error",
            "profile_name": profile_name,
            "removed_count": removed,
            "failures": failures,
        }
    return {"status": "removed", "profile_name": profile_name, "removed_count": removed}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
