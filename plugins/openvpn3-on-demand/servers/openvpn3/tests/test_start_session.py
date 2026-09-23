# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise the connect state machine. Uses `patch_lookups` to skip the manager chain
where it doesn't affect the assertion."""

from __future__ import annotations

from _fakes import FakeConfig, FakeConfigManager, FakeSession, FakeSessionManager, Wrapped, fake_status


def test_already_connected_short_circuits(server, patch_lookups):
    existing = FakeSession(path="/x/9", properties={"config_name": "demo"})
    patch_lookups(sessions={"demo": [existing]})
    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnAlreadyConnected)
    assert result.profile_name == "demo"
    assert result.session.path == "/x/9"


def test_no_config_returns_error(server, patch_lookups):
    patch_lookups()
    result = server._start_session("missing", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "No openvpn3 config named" in result.message


def test_baseline_dns_scope_applied_when_no_overrides(server, patch_lookups, no_sleep, wire_managers):
    cfg = FakeConfig(name="demo")
    new_sess = FakeSession()
    patch_lookups(configs={"demo": [cfg]})
    mgr = FakeSessionManager(new_tunnel_session=new_sess)
    wire_managers(session_mgr=mgr)

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnConnectedOk)
    assert cfg.overrides_set == [("dns-scope", Wrapped("String", "tunnel"))]
    assert result.overrides_applied == {"dns-scope": "tunnel"}


def test_caller_overrides_win_over_baseline(server, patch_lookups, no_sleep, wire_managers):
    cfg = FakeConfig(name="demo")
    patch_lookups(configs={"demo": [cfg]})
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=FakeSession()))

    result = server._start_session("demo", overrides={"dns-scope": "global", "log-level": 4})

    assert isinstance(result, server.VpnConnectedOk)
    assert ("dns-scope", Wrapped("String", "global")) in cfg.overrides_set
    # ints stringify — openvpn3's SetOverride only accepts bool/string variants.
    assert ("log-level", Wrapped("String", "4")) in cfg.overrides_set
    assert result.overrides_applied == {"dns-scope": "global", "log-level": 4}


def test_set_override_failure_returns_error(server, patch_lookups):
    cfg = FakeConfig(name="demo", raise_on_override="dns-scope")
    patch_lookups(configs={"demo": [cfg]})

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "SetOverride 'dns-scope' failed" in result.message


def test_new_tunnel_failure_returns_error(server, patch_lookups, wire_managers):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    wire_managers(session_mgr=FakeSessionManager(raise_on_new_tunnel=True))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "NewTunnel failed" in result.message


def test_ready_timeout_disconnects_and_errors(server, patch_lookups, no_sleep, monkeypatch, wire_managers):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(ready_always_fails=True)
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=sess))
    # Jump past the 15s budget on the third tick so we exercise the loop without spinning.
    ticks = iter([0.0, 1.0, 99.0, 99.5, 100.0])
    monkeypatch.setattr(server.time, "monotonic", lambda: next(ticks))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "Backend not ready" in result.message
    assert sess.disconnect_calls == 1


def test_connect_failure_returns_error(server, patch_lookups, no_sleep, wire_managers):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(raise_on_connect=True)
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=sess))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "Connect failed" in result.message
    # NewTunnel already registered the session; left alive it would read as already_connected next time.
    assert sess.disconnect_calls == 1


def test_connect_failure_does_not_strand_the_session(server, no_sleep, wire_managers):
    # Real manager chain: NewTunnel registers the session under the config name, like the daemon does.
    sess = FakeSession(raise_on_connect=True, properties={"config_name": "demo"})
    wire_managers(
        config_mgr=FakeConfigManager(configs_by_name={"demo": [FakeConfig(name="demo")]}),
        session_mgr=FakeSessionManager(new_tunnel_session=sess),
    )

    first = server.vpn_connect("demo")
    second = server.vpn_connect("demo")
    assert isinstance(first, server.VpnError)
    # The retry must try again, not report the dead session as already_connected.
    assert isinstance(second, server.VpnError)
    assert "Connect failed" in second.message
    assert sess.connect_calls == 2


def test_success_populates_session_view(server, patch_lookups, no_sleep, wire_managers):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(path="/p/new", properties={"config_name": "demo"})
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=sess))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnConnectedOk)
    assert result.profile_name == "demo"
    assert result.session.path == "/p/new"
    assert sess.connect_calls == 1


def test_start_session_surfaces_terminal_failure_status(server, patch_lookups, no_sleep, wire_managers):
    # openvpn3 core rejects the config a few ms after Connect() — we must surface that, not lie about success.
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(status=fake_status("CONNECTION", "CONN_FAILED", "UNUSED_OPTIONS_ERROR: Got unused options: foo"))
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=sess))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "Connect failed" in result.message
    assert "CONN_FAILED" in result.message
    assert "UNUSED_OPTIONS_ERROR" in result.message
    assert sess.disconnect_calls == 1


def test_start_session_times_out_if_status_stays_connecting(server, patch_lookups, no_sleep, fast_clock, wire_managers):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(status=fake_status("CONNECTION", "CONN_CONNECTING", "TUN/TAP setup"))
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=sess))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "did not reach CONN_CONNECTED" in result.message
    assert "CONN_CONNECTING" in result.message
    assert sess.disconnect_calls == 1


def test_start_session_errors_when_session_vanishes_mid_handshake(server, patch_lookups, no_sleep, wire_managers):
    # Single-use configs whose tunnel-start fails are reaped together with their session — GetStatus throws.
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(raise_on_status=True)
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=sess))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "vanished" in result.message
    # GetStatus may have failed only transiently; tear down so a late-starting session isn't left untracked.
    assert sess.disconnect_calls == 1


# Overrides already held by the profile ----------------------------------------


def test_overrides_already_held_are_not_rewritten(server, patch_lookups, no_sleep, wire_managers):
    # SetOverride rewrites a persistent BYO profile on disk; an identical value needs no write.
    cfg = FakeConfig(name="demo", overrides={"dns-scope": Wrapped("String", "tunnel")})
    patch_lookups(configs={"demo": [cfg]})
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=FakeSession()))

    result = server._start_session("demo", overrides={"log-level": 4})

    assert isinstance(result, server.VpnConnectedOk)
    assert cfg.overrides_set == [("log-level", Wrapped("String", "4"))]
    assert result.overrides_applied == {"dns-scope": "tunnel", "log-level": 4}


def test_override_with_a_different_value_is_sent(server, patch_lookups, no_sleep, wire_managers):
    cfg = FakeConfig(name="demo", overrides={"dns-scope": Wrapped("String", "global")})
    patch_lookups(configs={"demo": [cfg]})
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=FakeSession()))

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnConnectedOk)
    assert cfg.overrides_set == [("dns-scope", Wrapped("String", "tunnel"))]


def test_unreadable_overrides_fall_back_to_sending_all(server, patch_lookups, no_sleep, wire_managers):
    cfg = FakeConfig(name="demo", raise_on_get_overrides=True)
    patch_lookups(configs={"demo": [cfg]})
    wire_managers(session_mgr=FakeSessionManager(new_tunnel_session=FakeSession()))

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnConnectedOk)
    assert cfg.overrides_set == [("dns-scope", Wrapped("String", "tunnel"))]


# A session that predates the call ----------------------------------------------


def test_existing_paused_session_is_reported_not_reused(server, patch_lookups, wire_managers):
    existing = FakeSession(status=fake_status("CONNECTION", "CONN_PAUSED", "User request"))
    patch_lookups(sessions={"demo": [existing]}, configs={"demo": [FakeConfig(name="demo")]})
    _, mgr = wire_managers()

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnError)
    assert "CONN_PAUSED" in result.message
    assert "openvpn3 session-manage --disconnect --config demo" in result.message
    # Might be the user's own session — never torn down, and no second tunnel next to it.
    assert existing.disconnect_calls == 0
    assert mgr.new_tunnel_calls == []


def test_existing_stale_session_points_at_cleanup(server, patch_lookups, wire_managers):
    # Backend process died: the session manager still lists it but GetStatus fails.
    existing = FakeSession(raise_on_status=True)
    patch_lookups(sessions={"demo": [existing]}, configs={"demo": [FakeConfig(name="demo")]})
    _, mgr = wire_managers()

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnError)
    assert "isn't answering" in result.message
    assert "openvpn3 session-manage --cleanup" in result.message
    assert existing.disconnect_calls == 0
    assert mgr.new_tunnel_calls == []


def test_existing_connecting_session_is_waited_for(server, patch_lookups, no_sleep):
    existing = FakeSession(
        status_sequence=[
            fake_status("CONNECTION", "CONN_RECONNECTING"),
            fake_status("CONNECTION", "CONN_CONNECTING"),
            fake_status("CONNECTION", "CONN_CONNECTED"),
        ]
    )
    patch_lookups(sessions={"demo": [existing]})

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnAlreadyConnected)
    assert "CONN_CONNECTED" in result.session.status


def test_existing_connecting_session_is_left_alone_on_timeout(server, patch_lookups, no_sleep, fast_clock):
    existing = FakeSession(status=fake_status("CONNECTION", "CONN_RECONNECTING"))
    patch_lookups(sessions={"demo": [existing]})

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnError)
    assert "did not reach CONN_CONNECTED" in result.message
    assert "left in place" in result.message
    assert existing.disconnect_calls == 0


def test_existing_session_failing_while_waited_for_is_left_alone(server, patch_lookups, no_sleep):
    existing = FakeSession(status_sequence=[fake_status("CONNECTION", "CONN_CONNECTING"), fake_status("CONNECTION", "CONN_AUTH_FAILED")])
    patch_lookups(sessions={"demo": [existing]})

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnError)
    assert "CONN_AUTH_FAILED" in result.message
    assert "session-manage --disconnect --config demo" in result.message
    assert existing.disconnect_calls == 0


# D-Bus failures ----------------------------------------------------------------


def test_session_lookup_failure_is_a_dbus_error(server, wire_managers):
    wire_managers(session_mgr=FakeSessionManager(raise_on_lookup=True))
    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert result.message == "D-Bus error: lookup failed"
    assert result.profile_name == "demo"


def test_config_lookup_failure_is_not_reported_as_missing_config(server, wire_managers):
    wire_managers(config_mgr=FakeConfigManager(raise_on_lookup=True))
    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "D-Bus error" in result.message
    assert "Import it first" not in result.message
