#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Safety-net teardown for openvpn3-on-demand.

Fires on Stop and SessionEnd. Reads the project's
.claude/openvpn3-on-demand.local.md (YAML frontmatter) to learn which
mode the project uses, then disconnects that single profile iff it's
active. BYO mode (profile_name): disconnect that named session, leave
its config alone. Ephemeral mode (ovpn_provision_cmd): disconnect — and
remove the config for — ``ovpn3-od-<CLAUDE_CODE_SESSION_ID>`` (the id
comes from the environment, or from the hook's JSON stdin as a
fallback). Misconfigured (both fields, or neither) or no session id:
silent no-op. Never raises to the hook runner.

Talks to openvpn3 over D-Bus using the ``openvpn3`` Python module shipped
with openvpn3-client. If either dbus-python or openvpn3 isn't importable
(minimal container, missing package), this silently exits 0 — the hook is
a safety net, not a hard requirement.

Scoping rule: only touches the session whose config name matches the
profile declared in the per-project settings file. Never
blanket-disconnects.
"""

from __future__ import annotations

import json
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


def _remove_config_via_dbus(profile: str) -> None:
    """Best-effort: remove every openvpn3 config registered under ``profile``.

    Mirrors ``_disconnect_via_dbus`` but against the configuration manager.
    Ephemeral configs are imported single-use and openvpn3 normally drops them
    once a tunnel starts, so this is usually a no-op; it covers the case where a
    turn imported a config but its NewTunnel threw before consuming it. Swallows
    everything — never raises to the hook runner.
    """
    try:
        import dbus  # type: ignore[import-not-found]
        import openvpn3  # type: ignore[import-not-found]
    except ImportError:
        return  # silent: _disconnect_via_dbus runs first and prints the breadcrumb
    try:
        mgr = openvpn3.ConfigurationManager(dbus.SystemBus())
        paths = mgr.LookupConfigName(profile)
    except (dbus.exceptions.DBusException, RuntimeError):
        return
    if not paths:
        return
    for p in paths:
        try:
            mgr.Retrieve(p).Remove()
        except (dbus.exceptions.DBusException, RuntimeError):
            continue


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


def _session_id_from_stdin() -> str | None:
    """Best-effort: the Claude session id from the hook's JSON stdin payload.

    Stop / SessionEnd hooks receive a JSON object on stdin with a ``session_id``
    field. We only consult stdin when ``CLAUDE_CODE_SESSION_ID`` wasn't in the
    environment. If stdin is a tty (someone ran this script by hand) or isn't
    valid JSON, return None instead of blocking or raising — this is a safety
    net, not a hard dependency.
    """
    try:
        if sys.stdin.isatty():
            return None
        data = json.load(sys.stdin)
    except Exception:
        return None
    if isinstance(data, dict):
        sid = data.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
    return None


def _resolve_target(
    fields: dict[str, str], session_id: str | None
) -> tuple[str | None, bool]:
    """Map settings-file frontmatter to (config name to tear down, also remove its config?).

    - ``profile_name`` set, no ``ovpn_provision_cmd`` → BYO: disconnect that named
      session; leave its config alone (it's the user's).
    - ``ovpn_provision_cmd`` set, no ``profile_name`` → ephemeral: disconnect and
      remove the config for ``ovpn3-od-<session_id>``; needs a session id.
    - both set → misconfigured; the skill won't have started anything → no-op.
    - neither, or ephemeral with no session id → no-op.

    Every no-op case returns ``(None, False)``.
    """
    profile = fields.get("profile_name")
    provision = fields.get("ovpn_provision_cmd")
    if profile and provision:
        return None, False
    if profile:
        return profile, False
    if provision:
        if not session_id:
            return None, False
        return f"ovpn3-od-{session_id}", True
    return None, False


def main() -> int:
    if not STATE_FILE.is_file():
        return 0
    fields = _frontmatter_fields(
        STATE_FILE, "profile_name", "ovpn_provision_cmd", "post_disconnect_cmd"
    )
    # Only consult the environment / stdin for a session id when we'd actually
    # use one (ephemeral mode), so a manual `teardown.py` run on a tty doesn't
    # block in json.load(sys.stdin).
    profile = fields.get("profile_name")
    provision = fields.get("ovpn_provision_cmd")
    session_id: str | None = None
    if provision and not profile:
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or _session_id_from_stdin()
    target, also_remove = _resolve_target(fields, session_id)
    if not target:
        return 0
    disconnected = _disconnect_via_dbus(target)
    if also_remove:
        _remove_config_via_dbus(target)
    if disconnected:
        post_cmd = fields.get("post_disconnect_cmd")
        if post_cmd:
            run_post_disconnect(post_cmd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
