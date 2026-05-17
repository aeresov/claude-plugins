# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import pytest

from _fakes import FakeSession


@pytest.fixture
def counted_sessions(monkeypatch, server):
    """Return a fake `_sessions_for` that returns [FakeSession()] for the first N calls, then []."""

    def _factory(calls_until_empty: int):
        state = {"n": 0}

        def _sf(_name: str):
            state["n"] += 1
            return [FakeSession()] if state["n"] <= calls_until_empty else []

        monkeypatch.setattr(server, "_sessions_for", _sf)
        return state

    return _factory


def test_wait_returns_true_when_immediately_clear(server, no_sleep, counted_sessions):
    counted_sessions(0)
    assert server._wait_session_cleared("demo", timeout=1.0) is True


def test_wait_returns_true_after_a_few_polls(server, no_sleep, counted_sessions):
    counted_sessions(3)
    assert server._wait_session_cleared("demo", timeout=5.0) is True


def test_wait_returns_false_on_timeout(server, no_sleep, monkeypatch):
    monkeypatch.setattr(server, "_sessions_for", lambda _n: [FakeSession()])
    # Advance the clock past `timeout` after the first poll so the loop bails out.
    ticks = iter([0.0, 10.0, 10.1, 10.2, 10.3, 10.4, 10.5])
    monkeypatch.setattr(server.time, "monotonic", lambda: next(ticks))
    assert server._wait_session_cleared("demo", timeout=0.5) is False
