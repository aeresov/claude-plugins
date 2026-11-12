# SPDX-License-Identifier: AGPL-3.0-only
"""OpenVPN 2 backend, unprivileged side: for hosts without openvpn3-linux (Amazon Linux 2023, RHEL-likes without the
upstream repo) where the distribution's own ``openvpn`` 2.6 package is what there is.

A tunnel is one transient systemd unit running ``openvpn``. Reading its state needs no privileges and happens here;
starting and stopping it needs root and goes through ``ovpn2_helper.py`` under ``sudo -n`` — that file, not this one, is
where a profile is vetted and the routes are checked, because it is the part that runs as root."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import ovpn2_helper as helper

# Room for the helper's own connect deadline plus starting and, on failure, stopping the unit.
_HELPER_TIMEOUT_SECS: float = helper.CONNECT_TIMEOUT_SECS + 45.0

SUDO_HINT = (
    "The OpenVPN 2 backend starts tunnels through `sudo -n`, which was refused. Either this user needs passwordless sudo, or install the helper "
    f"root-owned at {helper.INSTALLED_PATH} with a sudoers rule for it (the plugin README has both lines)."
)


class BackendError(Exception):
    """Something outside the tunnel itself failed (sudo, systemd, the helper); the message is for the user."""


def available() -> bool:
    return Path(helper.OPENVPN).is_file() and shutil.which("systemctl") is not None


def stop_hint(name: str) -> str:
    return f"stop it with `sudo systemctl stop {helper.unit_name(name)}`, then retry"


def _run_helper(args: list[str], *, input_text: str | None = None) -> dict[str, Any]:
    # Only starting is refused: a stale copy would vet a profile by another version's rules. Stopping vets nothing, the
    # tunnel is one that copy started, and its `down` is the one command a restricted sudoers rule leaves for closing it.
    if args[0] != "down" and helper.helper_state() == "stale":
        raise BackendError(
            f"the helper installed at {helper.INSTALLED_PATH} is not the one this plugin version ships, so it was not used. Reinstall it: `{helper.reinstall_hint()}`"
        )
    argv = helper.root_argv() + args
    try:
        proc = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=_HELPER_TIMEOUT_SECS, check=False)
    except FileNotFoundError as exc:
        raise BackendError(f"cannot run {argv[0]}: {exc.strerror or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"the OpenVPN 2 helper gave no answer within {_HELPER_TIMEOUT_SECS:g}s; check `systemctl status '{helper.UNIT_PREFIX}*'`") from exc
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = None
    if not isinstance(result, dict) or "status" not in result:
        # No JSON means the helper never ran: sudo refused, or python3 isn't where it is expected.
        stderr = proc.stderr.strip().splitlines()
        detail = stderr[-1] if stderr else f"exit status {proc.returncode}"
        raise BackendError(f"{SUDO_HINT} (sudo said: {detail})" if "sudo" in detail or "password" in detail else f"the OpenVPN 2 helper failed: {detail}")
    return result


def session(name: str) -> dict[str, str] | None:
    """What systemd knows about ``name``'s tunnel, or None when there is none. Raises helper.Refused on a bad name or
    when systemd can't be asked — "couldn't find out" must not read as "not connected"."""
    info = helper.describe(name, helper.show_unit(helper.unit_name(name)))
    return None if info["state"] == "absent" else info


def sessions() -> list[dict[str, str]]:
    return helper.cmd_status(None)["sessions"]


def wait_connected(name: str, timeout: float) -> dict[str, str] | None:
    """Poll a tunnel that was already connecting when we looked; returns its last state (None if it went away)."""
    deadline = time.monotonic() + timeout
    while True:
        info = session(name)
        if info is None or info["state"] != "connecting" or time.monotonic() >= deadline:
            return info
        time.sleep(0.2)


def up(name: str, profile_text: str | None) -> dict[str, Any]:
    """Start ``name``. ``profile_text`` None means bring-your-own: the helper reads the profile from /etc/openvpn/client."""
    if profile_text is None:
        return _run_helper(["up", name, "--byo"])
    return _run_helper(["up", name], input_text=profile_text)


def down(name: str) -> dict[str, Any]:
    return _run_helper(["down", name])


def import_hint(name: str) -> str:
    return f"sudo install -m 600 -o root -g root /path/to/file.ovpn {helper.BYO_DIR}/{name}.conf"


def connect_notes(result: dict[str, Any]) -> list[str]:
    notes = [f"Routes through the tunnel: {', '.join(result.get('routes') or []) or 'none'}."]
    if result.get("skipped_routes"):
        notes.append(f"Routes the VPN pushed that were not installed: {'; '.join(result['skipped_routes'])}. This backend only carries private ranges.")
    if result.get("runs_as") == "root":
        notes.append("OpenVPN keeps running as root for this tunnel: there is no `openvpn` account to drop to (`sudo adduser --system --group openvpn` creates one).")
    if result.get("shadowed_routes"):
        notes.append(f"Routes the tunnel has but this host does not use for now: {'; '.join(result['shadowed_routes'])}.")
    notes.append("DNS is left alone on this backend: DNS servers and domains pushed by the VPN or set in the profile are ignored, and names resolve as they did before.")
    notes.append(
        f"The tunnel closes itself after {helper.INACTIVE_SECS // 60} min with next to no traffic, after {helper.MAX_LIFETIME_SECS // 3600} h in total, "
        "or when the VPN server stops answering; connecting again is safe."
    )
    return notes
