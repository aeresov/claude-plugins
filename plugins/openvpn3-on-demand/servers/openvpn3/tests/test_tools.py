# SPDX-License-Identifier: AGPL-3.0-only
"""Top-level @mcp.tool functions. They still behave as plain Python callables."""

from __future__ import annotations

import pytest

from _fakes import FakeConfig, FakeConfigManager, FakeSession, FakeSessionManager

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


@pytest.fixture(autouse=True)
def _restore_managers(server):
    original_session = server._get_session_mgr
    original_config = server._get_config_mgr
    yield
    server._get_session_mgr = original_session  # type: ignore[assignment]
    server._get_config_mgr = original_config  # type: ignore[assignment]
