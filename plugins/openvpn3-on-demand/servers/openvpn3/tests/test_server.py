# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for the openvpn3 MCP tool dispatch.

The server talks to openvpn3's D-Bus services via the ``openvpn3`` Python
module. Tests mock the Session/Configuration manager factories so nothing
here requires a live D-Bus bus, a running openvpn3 backend, or even the
system libraries (see ``conftest.py`` for the sys.modules stubs).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import dbus.exceptions  # installed as a stub by conftest.py
from openvpn3_mcp import server


def _make_session(config_name: str) -> MagicMock:
    sess = MagicMock()
    sess.GetPath.return_value = f"/net/openvpn/v3/sessions/{config_name}"
    sess.GetProperty.side_effect = lambda k: config_name if k == "config_name" else ""
    major = MagicMock()
    major.name = "CONNECTION"
    minor = MagicMock()
    minor.name = "CONN_CONNECTED"
    sess.GetStatus.return_value = {"major": major, "minor": minor, "message": "online"}
    return sess


# ---------- vpn_status ---------------------------------------------------


def test_vpn_status_empty():
    mgr = MagicMock()
    mgr.FetchAvailableSessions.return_value = []
    with patch.object(server, "_get_session_mgr", return_value=mgr):
        assert server.vpn_status() == {"session_count": 0, "sessions": []}


def test_vpn_status_lists_sessions():
    sess = _make_session("aiosws-vpn")
    mgr = MagicMock()
    mgr.FetchAvailableSessions.return_value = [sess]
    with patch.object(server, "_get_session_mgr", return_value=mgr):
        result = server.vpn_status()
    assert result["session_count"] == 1
    assert result["sessions"][0]["config_name"] == "aiosws-vpn"
    assert "CONN_CONNECTED" in result["sessions"][0]["status"]


# ---------- vpn_connect --------------------------------------------------


def test_vpn_connect_already_connected():
    sess_mgr = MagicMock()
    sess_mgr.LookupConfigName.return_value = ["/net/openvpn/v3/sessions/existing"]
    sess_mgr.Retrieve.return_value = _make_session("my-vpn")
    with patch.object(server, "_get_session_mgr", return_value=sess_mgr):
        result = server.vpn_connect(profile_name="my-vpn")
    assert result["status"] == "already_connected"
    assert result["profile_name"] == "my-vpn"


def test_vpn_connect_errors_when_config_missing():
    sess_mgr = MagicMock()
    sess_mgr.LookupConfigName.return_value = []
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = []
    with (
        patch.object(server, "_get_session_mgr", return_value=sess_mgr),
        patch.object(server, "_get_config_mgr", return_value=cfg_mgr),
    ):
        result = server.vpn_connect(profile_name="unknown")
    assert result["status"] == "error"
    assert "unknown" in result["message"]


def test_vpn_connect_happy_path():
    existing_sess_mgr = MagicMock()
    existing_sess_mgr.LookupConfigName.return_value = []  # not already connected

    cfg = MagicMock()
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = ["/cfg/one"]
    cfg_mgr.Retrieve.return_value = cfg

    new_sess = _make_session("my-vpn")
    new_sess.Ready.return_value = None  # first call succeeds

    sess_mgr = MagicMock()
    sess_mgr.LookupConfigName.return_value = []
    sess_mgr.NewTunnel.return_value = new_sess

    # _get_session_mgr is called multiple times; it must always hand back the
    # same mock so the lookup-then-create flow is coherent.
    with (
        patch.object(server, "_get_session_mgr", return_value=sess_mgr),
        patch.object(server, "_get_config_mgr", return_value=cfg_mgr),
    ):
        result = server.vpn_connect(profile_name="my-vpn")

    assert result["status"] == "connected"
    sess_mgr.NewTunnel.assert_called_once_with(cfg)
    new_sess.Connect.assert_called_once()


def test_vpn_connect_bails_when_ready_keeps_failing():
    cfg = MagicMock()
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = ["/cfg/one"]
    cfg_mgr.Retrieve.return_value = cfg

    new_sess = _make_session("my-vpn")
    new_sess.Ready.side_effect = dbus.exceptions.DBusException("needs credentials")

    sess_mgr = MagicMock()
    sess_mgr.LookupConfigName.return_value = []
    sess_mgr.NewTunnel.return_value = new_sess

    # Shrink the sleep so the test doesn't actually wait 15s in real time.
    with (
        patch.object(server, "_get_session_mgr", return_value=sess_mgr),
        patch.object(server, "_get_config_mgr", return_value=cfg_mgr),
        patch.object(server.time, "monotonic", side_effect=[0.0, 0.0, 100.0]),
        patch.object(server.time, "sleep"),
    ):
        result = server.vpn_connect(profile_name="my-vpn")

    assert result["status"] == "error"
    assert "not ready" in result["message"].lower() or "credentials" in result["message"].lower()
    new_sess.Disconnect.assert_called_once()
    new_sess.Connect.assert_not_called()


# ---------- vpn_disconnect -----------------------------------------------


def test_vpn_disconnect_requires_profile_name():
    result = server.vpn_disconnect(profile_name="")
    assert result["status"] == "error"


def test_vpn_disconnect_not_connected():
    sess_mgr = MagicMock()
    sess_mgr.LookupConfigName.return_value = []
    with patch.object(server, "_get_session_mgr", return_value=sess_mgr):
        assert server.vpn_disconnect(profile_name="my-vpn") == {
            "status": "not_connected",
            "profile_name": "my-vpn",
        }


def test_vpn_disconnect_tears_down_each_match_and_waits_cleared():
    sess_a = MagicMock()
    sess_b = MagicMock()
    sess_mgr = MagicMock()
    # First LookupConfigName call (from vpn_disconnect) returns both paths.
    # Subsequent calls come from _wait_session_cleared; return [] on the
    # first poll so the wait is trivially satisfied.
    sess_mgr.LookupConfigName.side_effect = [["/s/a", "/s/b"], []]
    sess_mgr.Retrieve.side_effect = [sess_a, sess_b]
    with patch.object(server, "_get_session_mgr", return_value=sess_mgr):
        result = server.vpn_disconnect(profile_name="my-vpn")
    assert result == {
        "status": "disconnected",
        "profile_name": "my-vpn",
        "session_cleared": True,
    }
    sess_a.Disconnect.assert_called_once()
    sess_b.Disconnect.assert_called_once()


def test_vpn_disconnect_reports_not_cleared_on_timeout():
    sess_mgr = MagicMock()
    # First lookup (inside vpn_disconnect) returns a path. Every subsequent
    # lookup (inside _wait_session_cleared) still returns a path, simulating
    # a session-manager that didn't finish teardown in time.
    sess_mgr.LookupConfigName.return_value = ["/s/stuck"]
    sess_mgr.Retrieve.return_value = MagicMock()
    with (
        patch.object(server, "_get_session_mgr", return_value=sess_mgr),
        # Make the poll wall-clock elapse instantly.
        patch.object(server.time, "monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]),
        patch.object(server.time, "sleep"),
    ):
        result = server.vpn_disconnect(profile_name="stuck-vpn")
    assert result["status"] == "disconnected"
    assert result["session_cleared"] is False


def test_vpn_disconnect_tolerates_stale_path_race():
    """Retrieve succeeds (dbus is lazy) but Disconnect fails with UnknownObject.

    Lookup returned a path that disappeared before we could act on it. The
    server should surface the failure in the error list, not crash.
    """
    sess_gone = MagicMock()
    sess_gone.Disconnect.side_effect = dbus.exceptions.DBusException(
        "org.freedesktop.DBus.Error.UnknownObject"
    )
    sess_mgr = MagicMock()
    sess_mgr.LookupConfigName.return_value = ["/s/gone"]
    sess_mgr.Retrieve.return_value = sess_gone
    with patch.object(server, "_get_session_mgr", return_value=sess_mgr):
        result = server.vpn_disconnect(profile_name="gone-vpn")
    assert result["status"] == "error"
    assert result["failures"] == ["org.freedesktop.DBus.Error.UnknownObject"]


# ---------- vpn_config_import --------------------------------------------


def test_vpn_config_import_missing_file(tmp_path):
    missing = tmp_path / "nope.ovpn"
    result = server.vpn_config_import(ovpn_path=str(missing), profile_name="x")
    assert result["status"] == "error"
    assert "File not found" in result["message"]


def test_vpn_config_import_already_imported(tmp_path):
    ovpn = tmp_path / "x.ovpn"
    ovpn.write_text("client\n")
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = ["/cfg/one"]
    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_import(ovpn_path=str(ovpn), profile_name="x")
    assert result == {"status": "already_imported", "profile_name": "x"}


def test_vpn_config_import_single_use(tmp_path):
    """single_use=True passes single_use=True, persistent=False to Import and echoes the flag back."""
    ovpn = tmp_path / "eph.ovpn"
    ovpn.write_text(
        "client\ndev tun\n"
        "<ca>\n-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n</ca>\n"
    )
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = []
    cfg_mgr.Import.return_value = MagicMock(
        GetPath=MagicMock(return_value="/net/openvpn/v3/configuration/eph")
    )
    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_import(
            ovpn_path=str(ovpn), profile_name="eph-vpn", single_use=True
        )
    assert result["status"] == "imported"
    assert result["single_use"] is True
    cfg_mgr.Import.assert_called_once()
    args, _ = cfg_mgr.Import.call_args
    name, _body, single_use_arg, persistent = args
    assert name == "eph-vpn"
    assert single_use_arg is True
    assert persistent is False


# ---------- vpn_config_remove --------------------------------------------


def test_vpn_config_remove_idempotent():
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = []
    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_remove(profile_name="gone")
    assert result == {"status": "already_removed", "profile_name": "gone"}


def test_vpn_config_remove_handles_duplicates():
    cfg_objs = [MagicMock(), MagicMock(), MagicMock()]
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = ["/c/1", "/c/2", "/c/3"]
    cfg_mgr.Retrieve.side_effect = cfg_objs
    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_remove(profile_name="dup")
    assert result == {"status": "removed", "profile_name": "dup", "removed_count": 3}
    for obj in cfg_objs:
        obj.Remove.assert_called_once()


def test_vpn_config_remove_reports_partial_failures():
    good = MagicMock()
    bad = MagicMock()
    bad.Remove.side_effect = dbus.exceptions.DBusException("config in use")
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = ["/c/1", "/c/2"]
    cfg_mgr.Retrieve.side_effect = [good, bad]
    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_remove(profile_name="mixed")
    assert result["status"] == "error"
    assert result["removed_count"] == 1
    assert result["failures"] == ["config in use"]


# ---------- dep guard ----------------------------------------------------


def test_vpn_status_surfaces_runtime_error_from_ping():
    """ConfigurationManager / SessionManager raise plain RuntimeError from their
    __ping helper when the bus service can't be reached. That must be caught
    and surfaced as {status: error}, not allowed to escape to FastMCP."""
    mgr = MagicMock()
    mgr.FetchAvailableSessions.side_effect = RuntimeError(
        "Could not establish contact with the Session Manager"
    )
    with patch.object(server, "_get_session_mgr", return_value=mgr):
        result = server.vpn_status()
    assert result["status"] == "error"
    assert "Session Manager" in result["message"]


def test_vpn_config_import_passes_raw_ovpn_contents(tmp_path):
    """The .ovpn file contents are handed verbatim to ConfigurationManager.Import.

    Regression: 0.4.0 ran files through ``openvpn3.ConfigParser`` (argparse-
    backed, openvpn2 CLI frontend), which rejected valid directives its
    whitelist didn't know — notably ``remote-random-hostname`` from AWS
    Client VPN exports. The fix is to let the configuration manager's
    authoritative parser (over D-Bus) handle the file.
    """
    # Realistic AWS Client VPN-style directive soup; many entries would fail
    # argparse-style parsing.
    ovpn_text = (
        "client\n"
        "dev tun\n"
        "proto udp\n"
        "remote cvpn-endpoint-deadbeef.prod.clientvpn.us-east-1.amazonaws.com 443\n"
        "remote-random-hostname\n"
        "resolv-retry infinite\n"
        "nobind\n"
        "persist-key\n"
        "persist-tun\n"
        "remote-cert-tls server\n"
        "cipher AES-256-GCM\n"
        "verb 3\n"
        "<ca>\n-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n</ca>\n"
        "<cert>\n-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n</cert>\n"
        "<key>\n-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n</key>\n"
        "reneg-sec 0\n"
    )
    ovpn = tmp_path / "aws.ovpn"
    ovpn.write_text(ovpn_text)

    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = []
    cfg_mgr.Import.return_value = MagicMock(
        GetPath=MagicMock(return_value="/net/openvpn/v3/configuration/aws")
    )

    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_import(ovpn_path=str(ovpn), profile_name="aws-vpn")

    assert result["status"] == "imported"
    # Verify the contents reached Import untouched — no argparse detour.
    cfg_mgr.Import.assert_called_once()
    args, _ = cfg_mgr.Import.call_args
    passed_name, passed_body, passed_single_use, passed_persistent = args
    assert passed_name == "aws-vpn"
    assert passed_body == ovpn_text
    assert passed_single_use is False
    assert passed_persistent is True


def test_vpn_config_import_surfaces_backend_parse_error(tmp_path):
    """If the backend rejects the config (bad directive, missing inline cert,
    whatever), the error message is the DBusException message verbatim — no
    glued-on "embed auth-user-pass" commentary that could mislead about the
    actual failure."""
    ovpn = tmp_path / "broken.ovpn"
    ovpn.write_text("garbage\n")

    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = []
    cfg_mgr.Import.side_effect = dbus.exceptions.DBusException(
        "Failed to parse configuration: unknown option on line 1"
    )

    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_import(ovpn_path=str(ovpn), profile_name="broken")

    assert result["status"] == "error"
    assert result["message"] == (
        "Import failed: Failed to parse configuration: unknown option on line 1"
    )
    # Regression guard: 0.4.0's message glued a misleading auth-user-pass
    # sentence onto every parse failure.
    assert "auth-user-pass" not in result["message"]


def test_tools_error_when_deps_missing(monkeypatch):
    monkeypatch.setattr(server, "_IMPORT_ERROR", ImportError("no dbus for you"))
    for call in (
        lambda: server.vpn_status(),
        lambda: server.vpn_connect(profile_name="x"),
        lambda: server.vpn_disconnect(profile_name="x"),
        lambda: server.vpn_config_import(ovpn_path="/tmp/x", profile_name="x"),
        lambda: server.vpn_config_remove(profile_name="x"),
    ):
        result = call()
        assert result["status"] == "error"
        assert "openvpn3-client" in result["message"] or "python3-dbus" in result["message"]
