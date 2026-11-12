# SPDX-License-Identifier: AGPL-3.0-only
"""The OpenVPN 2 backend as the tools see it: backend choice, the sudo bridge, and the four tools' contract — the same
idempotency and "never tear down what this call didn't start" rules the openvpn3 path has. The helper itself is faked."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openvpn3_mcp import ovpn2
from openvpn3_mcp import ovpn2_helper as helper

NAME = "ovpn-od-task-1"
UNIT = f"openvpn-on-demand-{NAME}.service"


def info(state: str, detail: str = "", name: str = NAME) -> dict[str, str]:
    return {"name": name, "unit": f"openvpn-on-demand-{name}.service", "state": state, "detail": detail}


CONNECTED = info("connected", helper.CONNECTED_STATUS)
UP_OK = {"status": "connected", "name": NAME, "unit": UNIT, "device": "ovodabc", "routes": ["10.0.0.0/16", "10.1.0.0/27"], "dropped_directives": ["dev", "dhcp-option"]}


class FakeOvpn2:
    """Replaces the pieces of `ovpn2` that would call systemd or sudo."""

    def __init__(self, monkeypatch, server) -> None:
        self.sessions: list[dict[str, str] | None] = [None]  # one per lookup; the last repeats
        self.up_result: dict[str, Any] | Exception = dict(UP_OK)
        self.down_result: dict[str, Any] | Exception = {"status": "disconnected", "name": NAME, "cleared": True}
        self.up_calls: list[tuple[str, str | None]] = []
        self.down_calls: list[str] = []
        self.lookup_error: Exception | None = None
        monkeypatch.setattr(server, "BACKEND", "openvpn2")
        monkeypatch.setattr(ovpn2, "session", self._session)
        monkeypatch.setattr(ovpn2, "sessions", lambda: [s for s in self.sessions if s])
        monkeypatch.setattr(ovpn2, "up", self._up)
        monkeypatch.setattr(ovpn2, "down", self._down)
        monkeypatch.setattr(ovpn2.time, "sleep", lambda _s: None)

    def _session(self, name: str) -> dict[str, str] | None:
        if self.lookup_error:
            raise self.lookup_error
        return self.sessions.pop(0) if len(self.sessions) > 1 else self.sessions[0]

    def _up(self, name: str, profile_text: str | None) -> dict[str, Any]:
        self.up_calls.append((name, profile_text))
        if isinstance(self.up_result, Exception):
            raise self.up_result
        return self.up_result

    def _down(self, name: str) -> dict[str, Any]:
        self.down_calls.append(name)
        if isinstance(self.down_result, Exception):
            raise self.down_result
        return self.down_result


@pytest.fixture
def fake(monkeypatch, server) -> FakeOvpn2:
    return FakeOvpn2(monkeypatch, server)


@pytest.fixture
def ovpn_file(tmp_path) -> Path:
    path = tmp_path / "task.ovpn"
    path.write_text("client\nremote vpn.example.com 443\n")
    return path


# backend choice --------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "have_dbus", "have_openvpn2", "chosen"),
    [
        ("", True, True, "openvpn3"),  # a host with both keeps what it had
        ("", True, False, "openvpn3"),
        ("", False, True, "openvpn2"),  # Amazon Linux 2023: no openvpn3-linux, openvpn from the distribution
        ("openvpn2", True, True, "openvpn2"),
        ("OpenVPN3 ", True, True, "openvpn3"),
    ],
)
def test_backend_selection(server, monkeypatch, env, have_dbus, have_openvpn2, chosen):
    monkeypatch.setenv(server.BACKEND_ENV, env)
    monkeypatch.setattr(server, "dbus", object() if have_dbus else None)
    monkeypatch.setattr(ovpn2, "available", lambda: have_openvpn2)
    monkeypatch.setattr(server.shutil, "which", lambda name: None)  # no openvpn3 CLI on the host
    assert server._select_backend() == chosen


def test_broken_openvpn3_install_is_an_error_not_a_reason_to_use_openvpn2(server, monkeypatch):
    # Ubuntu with openvpn3-client but without python3-dbus (or a venv that lost system site-packages), and the
    # distribution's openvpn package also present: before this backend existed that was exit 1 with an install hint.
    monkeypatch.delenv(server.BACKEND_ENV, raising=False)
    monkeypatch.setattr(server, "dbus", None)
    monkeypatch.setattr(ovpn2, "available", lambda: True)
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/openvpn3" if name == "openvpn3" else None)
    with pytest.raises(SystemExit) as excinfo:
        server._select_backend()
    message = str(excinfo.value.code)
    assert "python3-dbus" in message and "system site-packages" in message and f"{server.BACKEND_ENV}=openvpn2" in message
    # Asking for OpenVPN 2 explicitly is still honoured on such a host.
    monkeypatch.setenv(server.BACKEND_ENV, "openvpn2")
    assert server._select_backend() == "openvpn2"


@pytest.mark.parametrize(("env", "have_dbus", "have_openvpn2"), [("", False, False), ("openvpn2", True, False), ("openvpn3", False, True), ("wireguard", True, True)])
def test_backend_selection_exits_when_nothing_usable_matches(server, monkeypatch, env, have_dbus, have_openvpn2):
    monkeypatch.setenv(server.BACKEND_ENV, env)
    monkeypatch.setattr(server, "dbus", object() if have_dbus else None)
    monkeypatch.setattr(ovpn2, "available", lambda: have_openvpn2)
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as excinfo:
        server._select_backend()
    assert excinfo.value.code not in (0, None)


def test_no_backend_message_names_both_installs(server, monkeypatch, capsys):
    monkeypatch.delenv(server.BACKEND_ENV, raising=False)
    monkeypatch.setattr(server, "dbus", None)
    monkeypatch.setattr(ovpn2, "available", lambda: False)
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        server._select_backend()
    err = capsys.readouterr().err
    assert "openvpn3-client" in err and "dnf install openvpn" in err


def test_server_imports_and_answers_on_a_host_without_dbus_and_openvpn3():
    # conftest puts fake dbus/openvpn3 modules in place before importing the server, so the module-level path an
    # Amazon Linux host takes (no dbus at all, backend chosen at import) runs nowhere else. Here it runs for real.
    script = """
import pathlib, shutil, sys
sys.modules["dbus"] = None       # `import dbus` raises ImportError, as on a host without python3-dbus
sys.modules["openvpn3"] = None
real_is_file, real_which = pathlib.Path.is_file, shutil.which
pathlib.Path.is_file = lambda self: str(self) == "/usr/sbin/openvpn" or real_is_file(self)
shutil.which = lambda name, *a, **kw: {"systemctl": "/usr/bin/systemctl", "openvpn3": None}.get(name, real_which(name, *a, **kw))
from openvpn3_mcp import ovpn2, server
assert server.BACKEND == "openvpn2" and server.dbus is None, (server.BACKEND, server.dbus)
ovpn2.sessions = lambda: []
print(server.vpn_status().model_dump_json())
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, env={**os.environ, "OPENVPN_OD_BACKEND": ""})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"status": "ok", "backend": "openvpn2", "session_count": 0, "sessions": []}


# vpn_status ------------------------------------------------------------------


def test_status_lists_tunnels_and_names_the_backend(server, fake):
    fake.sessions = [CONNECTED, info("connecting", "Pre-connection initialization successful", "corp")]
    result = server.vpn_status()
    assert result.status == "ok" and result.backend == "openvpn2" and result.session_count == 2
    assert result.sessions[0].model_dump() == {"path": UNIT, "config_name": NAME, "status": f"CONNECTED: {helper.CONNECTED_STATUS}"}
    assert result.sessions[1].status == "CONNECTING: Pre-connection initialization successful"


def test_status_failure_is_an_error_not_an_empty_list(server, fake, monkeypatch):
    def boom():
        raise helper.Refused("systemctl list-units failed: Failed to connect to bus")

    monkeypatch.setattr(ovpn2, "sessions", boom)
    result = server.vpn_status()
    assert result.status == "error" and "Failed to connect to bus" in result.message


def test_openvpn3_results_still_say_openvpn3(server, wire_managers):
    wire_managers()
    assert server.vpn_status().backend == "openvpn3"


# connect ---------------------------------------------------------------------


def test_ephemeral_connect_hands_the_file_text_to_the_helper(server, fake, ovpn_file):
    result = server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-1")
    assert result.status == "connected" and result.backend == "openvpn2" and result.profile_name == NAME
    assert fake.up_calls == [(NAME, ovpn_file.read_text())]
    assert result.session.path == UNIT and result.session.status.startswith("CONNECTED")
    assert result.overrides_applied == {}
    notes = " ".join(result.notes)
    assert "10.0.0.0/16, 10.1.0.0/27" in notes and "DNS is left alone" in notes and "closes itself" in notes
    assert "dhcp-option" not in notes


def test_connect_says_which_pushed_routes_were_left_out(server, fake, ovpn_file):
    fake.up_result = {**UP_OK, "skipped_routes": ["8.8.8.0/24: outside the private ranges"]}
    result = server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-1")
    assert result.status == "connected"
    assert any("not installed: 8.8.8.0/24: outside the private ranges" in note for note in result.notes)
    assert not any("does not use for now" in note for note in result.notes) and not any("as root" in note for note in result.notes)
    fake.up_result = {**UP_OK, "runs_as": "root"}
    again = server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-2")
    assert any("keeps running as root" in note and "adduser --system --group openvpn" in note for note in again.notes)


def test_connect_says_which_routes_another_route_keeps_from_the_tunnel(server, fake, ovpn_file):
    # Two VPNs pushing the same prefix: the second tunnel has the route, but the first one's comes first in the table.
    fake.up_result = {**UP_OK, "shadowed_routes": ["10.77.0.0/16: ovod0123456789 already has a route for it and keeps that traffic until its route is gone"]}
    result = server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-1")
    assert result.status == "connected"
    assert any("does not use for now: 10.77.0.0/16: ovod0123456789 already has a route for it" in note for note in result.notes)


def test_byo_connect_reads_nothing_itself(server, fake):
    result = server.vpn_connect(profile_name=NAME)
    assert result.status == "connected" and fake.up_calls == [(NAME, None)]


def test_connect_is_idempotent_when_the_tunnel_is_up(server, fake, tmp_path):
    fake.sessions = [CONNECTED]
    # No file needed: an existing tunnel answers before anything is read.
    result = server.vpn_connect_ephemeral(ovpn_path=str(tmp_path / "gone.ovpn"), session_id="task-1")
    assert result.status == "already_connected" and result.backend == "openvpn2" and result.session.path == UNIT
    assert fake.up_calls == []


def test_connect_waits_for_a_tunnel_that_is_still_coming_up(server, fake):
    fake.sessions = [info("connecting"), info("connecting"), CONNECTED]
    result = server.vpn_connect(profile_name=NAME)
    assert result.status == "already_connected" and fake.up_calls == []


@pytest.mark.parametrize("state", ["failed", "stopping"])
def test_connect_reports_an_existing_tunnel_that_is_not_up_and_leaves_it(server, fake, state):
    fake.sessions = [info(state, "exit-code, exit status 1")]
    result = server.vpn_connect(profile_name=NAME)
    assert result.status == "error"
    assert "isn't connected" in result.message and "left in place" in result.message
    assert f"sudo systemctl stop {UNIT}" in result.message
    assert fake.up_calls == [] and fake.down_calls == []


def test_connect_reports_a_tunnel_that_never_finishes_connecting(server, fake, fast_clock):
    fake.sessions = [info("connecting")]
    result = server.vpn_connect(profile_name=NAME)
    assert result.status == "error" and "left in place" in result.message
    assert fake.up_calls == [] and fake.down_calls == []


def test_connect_that_vanishes_while_waited_for_starts_fresh(server, fake):
    fake.sessions = [info("connecting"), None]
    assert server.vpn_connect(profile_name=NAME).status == "connected"
    assert fake.up_calls == [(NAME, None)]


def test_lookup_failure_is_an_error_not_a_reason_to_start(server, fake, ovpn_file):
    fake.lookup_error = helper.Refused("systemctl show failed: Failed to connect to bus")
    for result in (server.vpn_connect(profile_name=NAME), server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-1")):
        assert result.status == "error" and "Failed to connect to bus" in result.message
    assert fake.up_calls == []


def test_a_tunnel_that_appeared_under_the_name_meanwhile_is_adopted_not_reported_as_a_failure(server, fake):
    # Two sessions sharing a profile name connect at once: the helper tells the second one the name is taken.
    fake.sessions = [None, info("connecting"), CONNECTED]
    fake.up_result = {"status": "exists", "name": NAME, "state": "connecting", "message": "unit … already exists (connecting); stop it with …"}
    result = server.vpn_connect(profile_name=NAME)
    assert result.status == "already_connected" and fake.down_calls == []


def test_a_tunnel_that_appeared_and_is_not_coming_up_is_reported_and_left_alone(server, fake):
    fake.sessions = [None, info("failed", "exit-code, exit status 1")]
    fake.up_result = {"status": "exists", "name": NAME, "state": "failed", "message": "unit … already exists (failed)"}
    result = server.vpn_connect(profile_name=NAME)
    assert result.status == "error" and "left in place" in result.message and fake.down_calls == []


def test_missing_byo_profile_says_how_to_import_it(server, fake):
    fake.up_result = {"status": "no_config", "name": "corp", "message": "No OpenVPN 2 profile at /etc/openvpn/client/corp.conf."}
    result = server.vpn_connect(profile_name="corp")
    assert result.status == "error"
    assert "Import it first" in result.message and "sudo install -m 600 -o root -g root /path/to/file.ovpn /etc/openvpn/client/corp.conf" in result.message


def test_helper_error_is_passed_on(server, fake, ovpn_file):
    fake.up_result = {"status": "error", "name": NAME, "message": "Connect failed: OpenVPN exited before the tunnel came up."}
    result = server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-1")
    assert result.status == "error" and result.message == "Connect failed: OpenVPN exited before the tunnel came up."


@pytest.mark.parametrize(
    "exc", [ovpn2.BackendError("sudo said: a password is required"), helper.Refused("bad name"), OSError("boom"), subprocess.TimeoutExpired(["systemctl"], 30)]
)
def test_backend_failures_become_errors(server, fake, ovpn_file, exc):
    fake.up_result = exc
    result = server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="task-1")
    assert result.status == "error" and result.message.startswith("OpenVPN 2 backend: ")


def test_overrides_are_openvpn3_settings_and_are_reported_not_applied(server, fake):
    result = server.vpn_connect(profile_name=NAME, overrides={"dns-scope": "global", "log-level": 4})
    assert result.status == "connected" and result.overrides_applied == {}
    assert any("config_overrides not applied (dns-scope, log-level)" in note for note in result.notes)


def test_ephemeral_file_problems_are_reported_like_on_openvpn3(server, fake, tmp_path):
    missing = server.vpn_connect_ephemeral(ovpn_path=str(tmp_path / "nope.ovpn"), session_id="task-1")
    assert missing.status == "error" and "File not found" in missing.message
    binary = tmp_path / "bad.ovpn"
    binary.write_bytes(b"client\n\xff\xfe")
    bad = server.vpn_connect_ephemeral(ovpn_path=str(binary), session_id="task-1")
    assert bad.status == "error" and "not valid UTF-8" in bad.message
    assert fake.up_calls == []


def test_ephemeral_still_requires_a_session_id(server, fake, ovpn_file):
    assert server.vpn_connect_ephemeral(ovpn_path=str(ovpn_file), session_id="  ").status == "error"


# disconnect ------------------------------------------------------------------


def test_disconnect_without_a_tunnel_is_not_connected_and_needs_no_sudo(server, fake):
    result = server.vpn_disconnect(profile_name=NAME)
    assert result.status == "not_connected" and fake.down_calls == []


def test_disconnect_stops_the_tunnel(server, fake):
    fake.sessions = [CONNECTED]
    result = server.vpn_disconnect(profile_name=NAME)
    assert result.status == "disconnected" and result.session_cleared is True and fake.down_calls == [NAME]


def test_disconnect_reports_a_tunnel_still_going(server, fake):
    fake.sessions = [CONNECTED]
    fake.down_result = {"status": "disconnected", "name": NAME, "cleared": False}
    assert server.vpn_disconnect(profile_name=NAME).session_cleared is False


def test_disconnect_failure_is_not_not_connected(server, fake):
    fake.lookup_error = helper.Refused("systemctl show failed")
    assert server.vpn_disconnect(profile_name=NAME).status == "error"
    fake.lookup_error = None
    fake.sessions = [CONNECTED]
    fake.down_result = {"status": "error", "name": NAME, "message": "systemctl stop failed: timeout"}
    result = server.vpn_disconnect(profile_name=NAME)
    assert result.status == "error" and "systemctl stop failed" in result.message


def test_disconnect_race_with_a_tunnel_that_just_went_away(server, fake):
    fake.sessions = [CONNECTED]
    fake.down_result = {"status": "not_connected", "name": NAME}
    assert server.vpn_disconnect(profile_name=NAME).status == "not_connected"


# the sudo bridge -------------------------------------------------------------


def fake_subprocess(monkeypatch, *, stdout: str = "", stderr: str = "", rc: int = 0, exc: Exception | None = None) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def run(argv, **kwargs):
        seen.append({"argv": argv, **kwargs})
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    monkeypatch.setattr(ovpn2.subprocess, "run", run)
    return seen


def test_bundled_helper_runs_under_the_system_python_in_isolated_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "INSTALLED_PATH", tmp_path / "not-installed")
    assert helper.root_argv() == ["sudo", "-n", "/usr/bin/python3", "-I", str(Path(helper.__file__).resolve())]


def test_installed_helper_is_preferred(monkeypatch, tmp_path):
    installed = tmp_path / "ovpn2-helper"
    installed.write_bytes(Path(helper.__file__).read_bytes())
    monkeypatch.setattr(helper, "INSTALLED_PATH", installed)
    assert helper.root_argv() == ["sudo", "-n", str(installed)]


def test_up_sends_the_profile_on_stdin_never_on_the_command_line(monkeypatch):
    seen = fake_subprocess(monkeypatch, stdout=json.dumps(UP_OK))
    assert ovpn2.up(NAME, "client\nremote x 443\n<key>\nSECRET\n</key>\n") == UP_OK
    assert seen[0]["argv"][-2:] == ["up", NAME] and seen[0]["input"].endswith("</key>\n")
    assert not any("SECRET" in arg for arg in seen[0]["argv"])
    assert ovpn2.up(NAME, None) == UP_OK and seen[1]["argv"][-3:] == ["up", NAME, "--byo"]


def test_sudo_refusal_is_explained(monkeypatch):
    fake_subprocess(monkeypatch, stderr="sudo: a password is required\n", rc=1)
    with pytest.raises(ovpn2.BackendError, match="passwordless sudo") as excinfo:
        ovpn2.down(NAME)
    assert "a password is required" in str(excinfo.value) and str(helper.INSTALLED_PATH) in str(excinfo.value)


def test_helper_crash_is_not_blamed_on_sudo(monkeypatch):
    fake_subprocess(monkeypatch, stderr="Traceback (most recent call last):\n  ...\nNameError: name 'x' is not defined\n", rc=1)
    with pytest.raises(ovpn2.BackendError, match="helper failed: NameError") as excinfo:
        ovpn2.down(NAME)
    assert "passwordless" not in str(excinfo.value)


def test_helper_timeout_and_missing_sudo(monkeypatch):
    fake_subprocess(monkeypatch, exc=subprocess.TimeoutExpired(["sudo"], 1))
    with pytest.raises(ovpn2.BackendError, match="no answer within"):
        ovpn2.down(NAME)
    fake_subprocess(monkeypatch, exc=FileNotFoundError(2, "No such file or directory"))
    with pytest.raises(ovpn2.BackendError, match="cannot run sudo"):
        ovpn2.down(NAME)


def test_stale_installed_helper_is_refused_before_anything_runs(monkeypatch, tmp_path):
    installed = tmp_path / "ovpn2-helper"
    installed.write_text("#!/usr/bin/python3 -I\n# an older plugin version's helper\n")
    monkeypatch.setattr(helper, "INSTALLED_PATH", installed)
    seen = fake_subprocess(monkeypatch, stdout=json.dumps(UP_OK))
    with pytest.raises(ovpn2.BackendError, match="not the one this plugin version ships") as excinfo:
        ovpn2.up(NAME, None)
    assert "sudo install -D -o root -g root -m 0755" in str(excinfo.value) and seen == []
    # Stopping is not refused: it vets nothing, the tunnel may be one that very copy started, and under a sudoers rule
    # limited to the installed helper its `down` is the only way left to close it.
    assert ovpn2.down(NAME) == UP_OK and seen.pop()["argv"] == ["sudo", "-n", str(installed), "down", NAME]
    # The same file as the bundled one is used as before.
    installed.write_bytes(Path(helper.__file__).read_bytes())
    assert ovpn2.up(NAME, None) == UP_OK and seen[0]["argv"][:3] == ["sudo", "-n", str(installed)]


def test_session_lookup_reads_systemd_without_sudo(monkeypatch, tmp_path):
    def run(argv, **kwargs):
        assert argv[0] == "systemctl", argv  # nothing privileged for a status read
        return subprocess.CompletedProcess(argv, 0, f"LoadState=loaded\nActiveState=active\nStatusText={helper.CONNECTED_STATUS}\n", "")

    monkeypatch.setattr(helper, "_run", run)
    monkeypatch.setattr(helper, "RUN_DIR", tmp_path)
    # Up as far as OpenVPN is concerned, but the helper hasn't marked it checked: not yet something to adopt.
    assert ovpn2.session(NAME) == info("connecting", "tunnel established, checks not finished")
    (tmp_path / f"{NAME}.ok").write_text("{}")
    assert ovpn2.session(NAME) == CONNECTED


def test_session_lookup_of_a_missing_unit_is_none(monkeypatch):
    monkeypatch.setattr(helper, "_run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "LoadState=not-found\nActiveState=inactive\n", ""))
    assert ovpn2.session(NAME) is None
