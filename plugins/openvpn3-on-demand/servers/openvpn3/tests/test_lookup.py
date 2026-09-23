# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import pytest

from _fakes import FakeConfig, FakeConfigManager, FakeDBusException, FakeSession, FakeSessionManager


def test_sessions_for_returns_retrieved_sessions(server, wire_managers):
    target = FakeSession(path="/x/1", properties={"config_name": "demo"})
    _, _ = wire_managers(session_mgr=FakeSessionManager(sessions_by_name={"demo": [target]}))
    out = server._sessions_for("demo")
    assert len(out) == 1
    assert out[0] is target


def test_sessions_for_returns_empty_when_nothing_matches(server, wire_managers):
    wire_managers(session_mgr=FakeSessionManager())
    assert server._sessions_for("missing") == []


def test_sessions_for_propagates_lookup_failure(server, wire_managers):
    # The daemons answer "no match" with []; an exception is a real failure and must not read as "nothing there".
    wire_managers(session_mgr=FakeSessionManager(raise_on_lookup=True))
    with pytest.raises(FakeDBusException):
        server._sessions_for("missing")


def test_sessions_for_propagates_retrieve_failure(server, wire_managers):
    sess = FakeSession()
    wire_managers(session_mgr=FakeSessionManager(sessions_by_name={"demo": [sess]}, raise_on_retrieve=True))
    with pytest.raises(RuntimeError):
        server._sessions_for("demo")


def test_configs_for_returns_retrieved_configs(server, wire_managers):
    cfg = FakeConfig(name="demo")
    wire_managers(config_mgr=FakeConfigManager(configs_by_name={"demo": [cfg]}))
    out = server._configs_for("demo")
    assert len(out) == 1
    assert out[0] is cfg


def test_configs_for_propagates_lookup_failure(server, wire_managers):
    wire_managers(config_mgr=FakeConfigManager(raise_on_lookup=True))
    with pytest.raises(FakeDBusException):
        server._configs_for("missing")
