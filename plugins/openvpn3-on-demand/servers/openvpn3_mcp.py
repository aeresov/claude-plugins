#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0"]
# ///
"""
openvpn3 MCP server.

Exposes four tools that shell out to the openvpn3 CLI:
  - vpn_status()
  - vpn_connect(profile_name)
  - vpn_disconnect(profile_name)
  - vpn_import(ovpn_path, profile_name)

Run via `uv run --script` (inline PEP 723 dependencies) or any Python 3.10+
environment with `mcp` installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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
    for s in _list_sessions():
        if s.get("config_name") == profile or s.get("config") == profile:
            return s
    return None


def _list_configs() -> list[str]:
    r = _run(OPENVPN3, "configs-list")
    names: list[str] = []
    for raw in r.stdout.splitlines():
        stripped = raw.strip()
        if stripped.lower().startswith("name:"):
            names.append(stripped.split(":", 1)[1].strip())
    return names


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
    return {"status": "disconnected", "profile_name": profile_name}


@mcp.tool()
def vpn_import(ovpn_path: str, profile_name: str) -> dict:
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
