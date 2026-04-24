#!/usr/bin/env python3
"""
Safety-net teardown for openvpn3-on-demand.

Fires on Stop and SessionEnd. Reads profile_name from the project's
.claude/openvpn3-on-demand.local.md (YAML frontmatter) and disconnects
that single profile iff it's currently active. Silent no-op otherwise;
never raises to the hook runner.

Scoping rule: only touches the session whose config name matches the
profile declared in the per-project settings file. Never
blanket-disconnects.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

STATE_FILE = Path(".claude/openvpn3-on-demand.local.md")
OPENVPN3 = "openvpn3"
SESSION_KEYS = {"config name", "config"}


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
    """Backwards-compatible wrapper kept so tests remain stable."""
    return _frontmatter_fields(path, "profile_name").get("profile_name")


def profile_is_active(profile: str) -> bool:
    r = subprocess.run(
        [OPENVPN3, "sessions-list"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if r.returncode != 0:
        return False
    for raw in r.stdout.splitlines():
        key, sep, value = raw.partition(":")
        if not sep:
            continue
        if key.strip().lower() in SESSION_KEYS and value.strip() == profile:
            return True
    return False


def disconnect(profile: str) -> None:
    subprocess.run(
        [OPENVPN3, "session-manage", "--config", profile, "--disconnect"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


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
    if shutil.which(OPENVPN3) is None:
        return 0
    fields = _frontmatter_fields(STATE_FILE, "profile_name", "post_disconnect_cmd")
    profile = fields.get("profile_name")
    if not profile:
        return 0
    if profile_is_active(profile):
        disconnect(profile)
        post_cmd = fields.get("post_disconnect_cmd")
        if post_cmd:
            run_post_disconnect(post_cmd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
