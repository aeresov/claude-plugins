#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Safety-net teardown for openvpn3-on-demand.

Fires on Stop and SessionEnd. Reads profile_name from the project's
.claude/openvpn3-on-demand.local.md (YAML frontmatter) and disconnects
that single profile iff it's currently active. Silent no-op otherwise;
never raises to the hook runner.

Talks to openvpn3 over D-Bus using the ``openvpn3`` Python module shipped
with openvpn3-client. If either dbus-python or openvpn3 isn't importable
(minimal container, missing package), this silently exits 0 — the hook is
a safety net, not a hard requirement.

Scoping rule: only touches the session whose config name matches the
profile declared in the per-project settings file. Never
blanket-disconnects.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Claude Code sets CLAUDE_PROJECT_DIR in the hook env; fall back to cwd so the
# hook still works if invoked by something that doesn't set it.
_PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
STATE_FILE = _PROJECT_DIR / ".claude/openvpn3-on-demand.local.md"


def _frontmatter_fields(path: Path, *fields: str) -> dict[str, str]:
    """Return the first occurrence of each requested field from the YAML frontmatter."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "---")
        end = next(
            i
            for i, ln in enumerate(lines[start + 1 :], start=start + 1)
            if ln.strip() == "---"
        )
    except StopIteration:
        return {}

    out: dict[str, str] = {}
    wanted = set(fields)
    for ln in lines[start + 1 : end]:
        key, sep, value = ln.partition(":")
        if not sep:
            continue
        k = key.strip()
        if k not in wanted or k in out:
            continue
        v = value.strip().strip('"').strip("'")
        if v:
            out[k] = v
    return out


def read_profile_name(path: Path) -> str | None:
    """Backwards-compatible wrapper kept so existing callers remain stable."""
    return _frontmatter_fields(path, "profile_name").get("profile_name")


def _disconnect_via_dbus(profile: str) -> bool:
    """Disconnect the session for ``profile`` over D-Bus. Returns True if a
    session was actually torn down, False if nothing matched or the D-Bus
    path failed and we couldn't even look up sessions.

    On ImportError we write a one-line breadcrumb to stderr before returning
    False, so a user whose openvpn3-client install is missing python3-dbus
    sees the reason their safety-net hook is inert.

    Callers should understand that D-Bus method calls from this hook have no
    explicit timeout beyond the dbus-python default (~25s); hooks.json caps
    the whole script at 10s and the runner SIGKILLs us on overrun. If
    net.openvpn.v3.sessions is hung we will be killed mid-call, not exit
    gracefully. That's acceptable for a safety-net hook — the consequence is
    at worst a stale session the user's next `vpn_connect` will notice.
    """
    try:
        import dbus  # type: ignore[import-not-found]
        import openvpn3  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            f"openvpn3-on-demand teardown: required dependency missing ({exc}); "
            "install python3-dbus and openvpn3-client. Skipping disconnect.",
            file=sys.stderr,
        )
        return False

    try:
        bus = dbus.SystemBus()
        mgr = openvpn3.SessionManager(bus)
        paths = mgr.LookupConfigName(profile)
    except (dbus.exceptions.DBusException, RuntimeError):
        return False

    if not paths:
        return False

    disconnected_any = False
    for p in paths:
        try:
            sess = mgr.Retrieve(p)
            sess.Disconnect()
            disconnected_any = True
        except (dbus.exceptions.DBusException, RuntimeError):
            continue
    return disconnected_any


def run_post_disconnect(cmd: str) -> None:
    """Best-effort run of the user's post_disconnect_cmd. Swallows all failures."""
    try:
        subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def main() -> int:
    if not STATE_FILE.is_file():
        return 0
    fields = _frontmatter_fields(STATE_FILE, "profile_name", "post_disconnect_cmd")
    profile = fields.get("profile_name")
    if not profile:
        return 0
    if _disconnect_via_dbus(profile):
        post_cmd = fields.get("post_disconnect_cmd")
        if post_cmd:
            run_post_disconnect(post_cmd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
