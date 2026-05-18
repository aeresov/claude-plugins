# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise the connect state machine. Uses `patch_lookups` to skip the manager chain
where it doesn't affect the assertion."""

from __future__ import annotations

import pytest

from _fakes import FakeConfig, FakeSession, FakeSessionManager, Wrapped


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


def test_baseline_dns_scope_applied_when_no_overrides(server, patch_lookups, no_sleep):
    cfg = FakeConfig(name="demo")
    new_sess = FakeSession()
    patch_lookups(configs={"demo": [cfg]})
    mgr = FakeSessionManager(new_tunnel_session=new_sess)
    server._get_session_mgr = lambda: mgr  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)

    assert isinstance(result, server.VpnConnectedOk)
    assert cfg.overrides_set == [("dns-scope", Wrapped("String", "tunnel"))]
    assert result.overrides_applied == {"dns-scope": "tunnel"}


def test_caller_overrides_win_over_baseline(server, patch_lookups, no_sleep):
    cfg = FakeConfig(name="demo")
    patch_lookups(configs={"demo": [cfg]})
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=FakeSession())  # type: ignore[assignment]

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


def test_new_tunnel_failure_returns_error(server, patch_lookups):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    server._get_session_mgr = lambda: FakeSessionManager(raise_on_new_tunnel=True)  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "NewTunnel failed" in result.message


def test_ready_timeout_disconnects_and_errors(server, patch_lookups, no_sleep, monkeypatch):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(ready_always_fails=True)
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=sess)  # type: ignore[assignment]
    # Jump past the 15s budget on the third tick so we exercise the loop without spinning.
    ticks = iter([0.0, 1.0, 99.0, 99.5, 100.0])
    monkeypatch.setattr(server.time, "monotonic", lambda: next(ticks))

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "Backend not ready" in result.message
    assert sess.disconnect_calls == 1


def test_connect_failure_returns_error(server, patch_lookups, no_sleep):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(raise_on_connect=True)
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=sess)  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "Connect failed" in result.message


def test_success_populates_session_view(server, patch_lookups, no_sleep):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(path="/p/new", properties={"config_name": "demo"})
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=sess)  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnConnectedOk)
    assert result.profile_name == "demo"
    assert result.session.path == "/p/new"
    assert sess.connect_calls == 1


def _status(major: str, minor: str, message: str = "") -> dict[str, object]:
    return {
        "major": type("E", (), {"name": major})(),
        "minor": type("E", (), {"name": minor})(),
        "message": message,
    }


def test_start_session_surfaces_terminal_failure_status(server, patch_lookups, no_sleep):
    # openvpn3 core rejects the config a few ms after Connect() — we must surface that, not lie about success.
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(status=_status("CONNECTION", "CONN_FAILED", "UNUSED_OPTIONS_ERROR: Got unused options: foo"))
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=sess)  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "Connect failed" in result.message
    assert "CONN_FAILED" in result.message
    assert "UNUSED_OPTIONS_ERROR" in result.message
    assert sess.disconnect_calls == 1


def test_start_session_times_out_if_status_stays_connecting(server, patch_lookups, no_sleep, fast_clock):
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(status=_status("CONNECTION", "CONN_CONNECTING", "TUN/TAP setup"))
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=sess)  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "did not reach CONN_CONNECTED" in result.message
    assert "CONN_CONNECTING" in result.message
    assert sess.disconnect_calls == 1


def test_start_session_errors_when_session_vanishes_mid_handshake(server, patch_lookups, no_sleep):
    # Single-use configs whose tunnel-start fails are reaped together with their session — GetStatus throws.
    patch_lookups(configs={"demo": [FakeConfig(name="demo")]})
    sess = FakeSession(raise_on_status=True)
    server._get_session_mgr = lambda: FakeSessionManager(new_tunnel_session=sess)  # type: ignore[assignment]

    result = server._start_session("demo", overrides=None)
    assert isinstance(result, server.VpnError)
    assert "vanished" in result.message


@pytest.fixture(autouse=True)
def _restore_managers(server):
    """Tests above poke `server._get_session_mgr` directly; restore after each test."""
    original_session = server._get_session_mgr
    original_config = server._get_config_mgr
    yield
    server._get_session_mgr = original_session  # type: ignore[assignment]
    server._get_config_mgr = original_config  # type: ignore[assignment]
