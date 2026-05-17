# SPDX-License-Identifier: AGPL-3.0-only
"""Pure helpers + pydantic model contracts."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from _fakes import FakeDBusException, Wrapped


def test_ephemeral_profile_name_format(server):
    assert server._ephemeral_profile_name("abc123") == "ovpn3-od-abc123"


@pytest.mark.parametrize(
    ("value", "expected_kind", "expected_value"),
    [
        (True, "Boolean", True),
        (False, "Boolean", False),
        (4, "Int32", 4),
        (0, "Int32", 0),
        ("tunnel", "String", "tunnel"),
        (3.14, "String", "3.14"),
        (None, "String", "None"),
    ],
)
def test_wrap_override_value_dispatch(server, value, expected_kind, expected_value):
    out = server._wrap_override_value(value)
    assert isinstance(out, Wrapped)
    assert out.kind == expected_kind
    assert out.value == expected_value


def test_wrap_override_value_bool_before_int_trap(server):
    # bool is an int subclass; if the match order ever flips, True/False marshal as Int32 and
    # openvpn3 silently rejects the override. Regression guard.
    assert server._wrap_override_value(True).kind == "Boolean"
    assert server._wrap_override_value(False).kind == "Boolean"


def test_dbus_error_msg_uses_get_dbus_message(server):
    exc = FakeDBusException("nope, no such config")
    assert server._dbus_error_msg(exc) == "nope, no such config"


def test_dbus_error_msg_falls_back_to_str(server):
    assert server._dbus_error_msg(RuntimeError("boom")) == "boom"


def test_vpn_status_discriminator_picks_ok(server):
    adapter = TypeAdapter(server.VpnStatusResult)
    parsed = adapter.validate_python({"status": "ok", "session_count": 0, "sessions": []})
    assert isinstance(parsed, server.VpnStatusOk)


def test_vpn_status_discriminator_picks_error(server):
    adapter = TypeAdapter(server.VpnStatusResult)
    parsed = adapter.validate_python({"status": "error", "message": "D-Bus down"})
    assert isinstance(parsed, server.VpnError)
    assert parsed.message == "D-Bus down"
    assert parsed.profile_name is None


def test_vpn_connect_discriminator_distinguishes_states(server):
    adapter = TypeAdapter(server.VpnConnectResult)
    session = {"path": "/p", "config_name": "demo", "status": "ok"}
    assert isinstance(
        adapter.validate_python({"status": "connected", "profile_name": "demo", "session": session, "overrides_applied": {}}),
        server.VpnConnectedOk,
    )
    assert isinstance(
        adapter.validate_python({"status": "already_connected", "profile_name": "demo", "session": session}),
        server.VpnAlreadyConnected,
    )


def test_vpn_disconnect_discriminator_distinguishes_states(server):
    adapter = TypeAdapter(server.VpnDisconnectResult)
    assert isinstance(
        adapter.validate_python({"status": "disconnected", "profile_name": "demo", "session_cleared": True}),
        server.VpnDisconnectedOk,
    )
    assert isinstance(
        adapter.validate_python({"status": "not_connected", "profile_name": "demo"}),
        server.VpnNotConnected,
    )
