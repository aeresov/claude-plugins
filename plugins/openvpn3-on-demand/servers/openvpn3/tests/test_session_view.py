# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from _fakes import FakeSession


def test_session_view_uses_config_name(server):
    sess = FakeSession(path="/p/1", properties={"config_name": "demo"})
    view = server._session_view(sess)
    assert view.path == "/p/1"
    assert view.config_name == "demo"
    assert "CONNECTION / CONN_CONNECTED" in view.status


def test_session_view_falls_back_to_session_name(server):
    sess = FakeSession(properties={"session_name": "fallback"}, raise_on_properties=("config_name",))
    assert server._session_view(sess).config_name == "fallback"


def test_session_view_unknown_when_both_keys_fail(server):
    sess = FakeSession(properties={}, raise_on_properties=("config_name", "session_name"))
    assert server._session_view(sess).config_name == "<unknown>"


def test_session_view_status_when_get_status_raises(server):
    sess = FakeSession(raise_on_status=True)
    assert server._session_view(sess).status.startswith("<unavailable: status unavailable")
