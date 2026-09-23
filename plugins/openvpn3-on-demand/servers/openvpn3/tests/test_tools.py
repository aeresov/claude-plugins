# SPDX-License-Identifier: AGPL-3.0-only
"""Top-level @mcp.tool functions. They still behave as plain Python callables."""

from __future__ import annotations

import pytest

from _fakes import FakeConfig, FakeConfigManager, FakeDBusException, FakeSession, FakeSessionManager, fake_status


@pytest.fixture
def bus_down(monkeypatch, server):
    """Both manager getters fail the way `dbus.SystemBus()` does with no reachable system bus."""

    def _raise():
        raise FakeDBusException("Failed to connect to socket /run/dbus/system_bus_socket")

    monkeypatch.setattr(server, "_get_session_mgr", _raise)
    monkeypatch.setattr(server, "_get_config_mgr", _raise)


# vpn_status ------------------------------------------------------------------


def test_vpn_status_lists_all_sessions(server, wire_managers):
    s1 = FakeSession(path="/p/1", properties={"config_name": "alpha"})
    s2 = FakeSession(path="/p/2", properties={"config_name": "beta"})
    wire_managers(session_mgr=FakeSessionManager(all_sessions=[s1, s2]))

    result = server.vpn_status()
    assert isinstance(result, server.VpnStatusOk)
    assert result.session_count == 2
    assert {v.config_name for v in result.sessions} == {"alpha", "beta"}


def test_vpn_status_returns_error_on_dbus_failure(server, wire_managers):
    wire_managers(session_mgr=FakeSessionManager(raise_on_fetch=True))
    result = server.vpn_status()
    assert isinstance(result, server.VpnError)
    assert "D-Bus error" in result.message


# vpn_connect -----------------------------------------------------------------


def test_vpn_connect_dispatches_to_start_session(server, patch_lookups, no_sleep, wire_managers):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=FakeSession(properties={"config_name": "demo"})))
    result = server.vpn_connect("demo")
    assert isinstance(result, server.VpnConnectedOk)
    assert result.profile_name == "demo"


def test_vpn_connect_returns_error_when_bus_unreachable(server, bus_down):
    result = server.vpn_connect("demo")
    assert isinstance(result, server.VpnError)
    assert result.message == "D-Bus error: Failed to connect to socket /run/dbus/system_bus_socket"
    assert result.profile_name == "demo"


# vpn_disconnect --------------------------------------------------------------


def test_vpn_disconnect_requires_profile_name(server):
    result = server.vpn_disconnect("")
    assert isinstance(result, server.VpnError)
    assert "profile_name is required" in result.message


def test_vpn_disconnect_no_session(server, patch_lookups):
    patch_lookups()
    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnNotConnected)
    assert result.profile_name == "demo"


def test_vpn_disconnect_partial_failure_joins_messages(server, patch_lookups, no_sleep):
    s1 = FakeSession(raise_on_disconnect=True, disconnect_error_msg="ebusy")
    s2 = FakeSession(raise_on_disconnect=True, disconnect_error_msg="eperm")
    patch_lookups(sessions={"demo": [s1, s2]})

    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnError)
    assert result.message == "ebusy; eperm"
    assert result.profile_name == "demo"


def test_vpn_disconnect_success_waits_for_clear(server, monkeypatch, no_sleep):
    sess = FakeSession()
    calls = {"sessions_for": 0}

    def _sessions_for(name):
        calls["sessions_for"] += 1
        # First call (the lookup before Disconnect) returns the session; subsequent (poll) returns [].
        return [sess] if calls["sessions_for"] == 1 else []

    monkeypatch.setattr(server, "_sessions_for", _sessions_for)

    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnDisconnectedOk)
    assert result.session_cleared is True
    assert sess.disconnect_calls == 1


def test_vpn_disconnect_lookup_failure_is_not_not_connected(server, wire_managers):
    # A failed lookup must not claim there's no tunnel — it may still be up.
    wire_managers(session_mgr=FakeSessionManager(raise_on_lookup=True))
    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnError)
    assert result.message == "D-Bus error: lookup failed"
    assert result.profile_name == "demo"


def test_vpn_disconnect_returns_error_when_bus_unreachable(server, bus_down):
    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnError)
    assert "D-Bus error" in result.message


def test_vpn_disconnect_retrieve_failure_returns_error(server, wire_managers):
    # The wrapper's Retrieve() pings the manager and raises RuntimeError when it can't reach it.
    wire_managers(session_mgr=FakeSessionManager(sessions_by_name={"demo": [FakeSession()]}, raise_on_retrieve=True))
    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnError)
    assert "Could not establish contact" in result.message


def test_vpn_disconnect_failing_polls_do_not_report_cleared(server, monkeypatch, no_sleep, fast_clock):
    sess = FakeSession()
    calls = {"sessions_for": 0}

    def _sessions_for(name):
        calls["sessions_for"] += 1
        if calls["sessions_for"] == 1:
            return [sess]
        raise RuntimeError("Could not establish contact with the Session Manager")

    monkeypatch.setattr(server, "_sessions_for", _sessions_for)

    result = server.vpn_disconnect("demo")
    assert isinstance(result, server.VpnDisconnectedOk)
    assert result.session_cleared is False


# vpn_connect_ephemeral -------------------------------------------------------


def test_vpn_connect_ephemeral_rejects_empty_session_id(server):
    result = server.vpn_connect_ephemeral("/tmp/x.ovpn", session_id="")
    assert isinstance(result, server.VpnError)
    assert "session_id is required" in result.message


def test_vpn_connect_ephemeral_returns_already_connected(server, patch_lookups):
    existing = FakeSession(path="/p/old", properties={"config_name": "ovpn3-od-sess-1"})
    patch_lookups(sessions={"ovpn3-od-sess-1": [existing]})

    result = server.vpn_connect_ephemeral("/tmp/does-not-matter.ovpn", session_id="sess-1")
    assert isinstance(result, server.VpnAlreadyConnected)
    assert result.profile_name == "ovpn3-od-sess-1"


def test_vpn_connect_ephemeral_reports_existing_paused_session(server, patch_lookups, wire_managers, tmp_path):
    existing = FakeSession(status=fake_status("CONNECTION", "CONN_PAUSED"))
    patch_lookups(sessions={"ovpn3-od-sess-p": [existing]})
    cfg_mgr, _ = wire_managers()
    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\n")

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-p")
    assert isinstance(result, server.VpnError)
    assert "CONN_PAUSED" in result.message
    assert "session-manage --disconnect --config ovpn3-od-sess-p" in result.message
    assert existing.disconnect_calls == 0
    assert cfg_mgr.import_calls == []


def test_vpn_connect_ephemeral_session_lookup_failure_skips_import(server, wire_managers, tmp_path):
    # Without the already_connected guard a second tunnel could be imported under the same name.
    cfg_mgr, _ = wire_managers(session_mgr=FakeSessionManager(raise_on_lookup=True))
    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\n")

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-l")
    assert isinstance(result, server.VpnError)
    assert result.message == "D-Bus error: lookup failed"
    assert result.profile_name == "ovpn3-od-sess-l"
    assert cfg_mgr.import_calls == []


def test_vpn_connect_ephemeral_returns_error_when_bus_unreachable(server, bus_down, tmp_path):
    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\n")
    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-b")
    assert isinstance(result, server.VpnError)
    assert "D-Bus error" in result.message


def test_vpn_connect_ephemeral_non_utf8_file_names_the_file(server, patch_lookups, wire_managers, tmp_path):
    patch_lookups()
    cfg_mgr, _ = wire_managers()
    ovpn = tmp_path / "latin1.ovpn"
    ovpn.write_bytes("# Soci\u00e9t\u00e9 VPN\nclient\n".encode("latin-1"))

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-u")
    assert isinstance(result, server.VpnError)
    assert str(ovpn) in result.message
    assert "not valid UTF-8" in result.message
    assert cfg_mgr.import_calls == []


def test_vpn_connect_ephemeral_file_not_found(server, patch_lookups, tmp_path):
    patch_lookups()
    missing = tmp_path / "missing.ovpn"
    result = server.vpn_connect_ephemeral(str(missing), session_id="sess-2")
    assert isinstance(result, server.VpnError)
    assert "File not found" in result.message
    assert result.profile_name == "ovpn3-od-sess-2"


def test_vpn_connect_ephemeral_removes_stale_config(server, monkeypatch, patch_lookups, wire_managers, tmp_path, no_sleep):
    stale = FakeConfig(name="ovpn3-od-sess-3")
    new_cfg = FakeConfig(name="ovpn3-od-sess-3")
    cfg_mgr = FakeConfigManager()
    session_mgr = FakeSessionManager(new_tunnel_session=FakeSession(properties={"config_name": "ovpn3-od-sess-3"}))
    wire_managers(config_mgr=cfg_mgr, session_mgr=session_mgr)

    # First _configs_for call (cleanup) → [stale]; second call (lookup for SetOverride) → [new_cfg].
    config_states = iter([[stale], [new_cfg]])
    monkeypatch.setattr(server, "_sessions_for", lambda _n: [])
    monkeypatch.setattr(server, "_configs_for", lambda _n: next(config_states))

    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\nremote example.com 1194\n")

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-3")
    assert isinstance(result, server.VpnConnectedOk)
    assert stale.removed is True
    assert cfg_mgr.import_calls == [
        {
            "name": "ovpn3-od-sess-3",
            "cfg": "client\nremote example.com 1194\n",
            "single_use": True,
            "persistent": False,
        }
    ]


def test_vpn_connect_ephemeral_import_failure(server, patch_lookups, wire_managers, tmp_path):
    patch_lookups()  # no sessions, no stale configs
    cfg_mgr = FakeConfigManager(raise_on_import=True)
    wire_managers(config_mgr=cfg_mgr)

    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\n")
    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-4")
    assert isinstance(result, server.VpnError)
    assert "Import failed" in result.message
    assert result.profile_name == "ovpn3-od-sess-4"


def test_vpn_connect_ephemeral_does_not_pre_parse_with_configparser(server, monkeypatch, wire_managers, tmp_path, no_sleep):
    """Regression guard: ConfigParser whitelist rejects valid directives like AWS Client VPN's `remote-random-hostname`.
    Server must hand raw bytes to ConfigurationManager.Import — no pre-parse."""
    cfg_mgr = FakeConfigManager()
    wire_managers(config_mgr=cfg_mgr, session_mgr=FakeSessionManager(new_tunnel_session=FakeSession()))

    new_cfg = FakeConfig(name="ovpn3-od-sess-5")
    config_states = iter([[], [new_cfg]])  # cleanup → empty; lookup-after-import → [new_cfg]
    monkeypatch.setattr(server, "_sessions_for", lambda _n: [])
    monkeypatch.setattr(server, "_configs_for", lambda _n: next(config_states))

    raw = "client\nremote-random-hostname\nremote vpn.example.com 1194\n"
    ovpn = tmp_path / "aws-cvpn.ovpn"
    ovpn.write_text(raw)

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-5")
    assert isinstance(result, server.VpnConnectedOk)
    # The exact bytes from disk must reach Import unchanged.
    assert cfg_mgr.import_calls[0]["cfg"] == raw


def test_vpn_connect_ephemeral_drops_the_config_when_the_tunnel_never_starts(server, monkeypatch, wire_managers, tmp_path, no_sleep):
    """NewTunnel consumes a single-use config; failing before it must not strand one."""
    imported = FakeConfig(name="ovpn3-od-sess-9")
    wire_managers(config_mgr=FakeConfigManager(), session_mgr=FakeSessionManager(raise_on_new_tunnel=True))
    monkeypatch.setattr(server, "_sessions_for", lambda _n: [])
    monkeypatch.setattr(server, "_configs_for", lambda _n: [imported])

    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\nremote example.com 1194\n")

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-9")
    assert isinstance(result, server.VpnError)
    assert "NewTunnel failed" in result.message
    assert imported.removed is True


def test_vpn_connect_ephemeral_cleanup_lookup_failure_keeps_the_original_error(server, monkeypatch, wire_managers, tmp_path, no_sleep):
    wire_managers(config_mgr=FakeConfigManager(), session_mgr=FakeSessionManager(raise_on_new_tunnel=True))
    monkeypatch.setattr(server, "_sessions_for", lambda _n: [])
    imported = FakeConfig(name="ovpn3-od-sess-c")
    # stale cleanup → []; lookup for SetOverride → [imported]; post-failure cleanup → D-Bus failure.
    states: list[object] = [[], [imported], FakeDBusException("lookup failed")]

    def _configs_for(_n):
        state = states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    monkeypatch.setattr(server, "_configs_for", _configs_for)
    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\n")

    result = server.vpn_connect_ephemeral(str(ovpn), session_id="sess-c")
    assert isinstance(result, server.VpnError)
    assert "NewTunnel failed" in result.message
