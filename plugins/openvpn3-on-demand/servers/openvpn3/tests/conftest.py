# SPDX-License-Identifier: AGPL-3.0-only
"""Inject fake dbus / openvpn3 into sys.modules *before* importing the server.

CI runs on stock ubuntu-latest without `python3-dbus` or `openvpn3-client`, so the real
modules aren't importable. Tests get a stub surface big enough that
`from openvpn3_mcp import server` succeeds; per-test fakes are wired in via fixtures."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from _fakes import FakeConfigManager, FakeDBusException, FakeSessionManager, Wrapped


def _install_stubs() -> None:
    if "openvpn3_mcp.server" in sys.modules:
        return

    # `Any` so attribute assignment on a fresh ModuleType type-checks.
    dbus_mod: Any = types.ModuleType("dbus")
    dbus_exc_mod: Any = types.ModuleType("dbus.exceptions")
    dbus_exc_mod.DBusException = FakeDBusException
    dbus_mod.exceptions = dbus_exc_mod
    dbus_mod.Boolean = lambda v: Wrapped("Boolean", v)
    dbus_mod.Int32 = lambda v: Wrapped("Int32", v)
    dbus_mod.String = lambda v: Wrapped("String", v)
    dbus_mod.SystemBus = lambda: object()
    sys.modules["dbus"] = dbus_mod
    sys.modules["dbus.exceptions"] = dbus_exc_mod

    openvpn3_mod: Any = types.ModuleType("openvpn3")
    openvpn3_mod.ConfigurationManager = lambda _bus: None
    openvpn3_mod.SessionManager = lambda _bus: None
    sys.modules["openvpn3"] = openvpn3_mod


_install_stubs()


from openvpn3_mcp import server as _server  # noqa: E402


@pytest.fixture
def server():
    return _server


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the wall-clock sleeps in poll loops so tests stay sub-millisecond."""
    monkeypatch.setattr(_server.time, "sleep", lambda _s: None)


@pytest.fixture
def fast_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``time.monotonic`` with a counter that advances 0.05s per call."""
    ticks = [0.0]

    def _now() -> float:
        ticks[0] += 0.05
        return ticks[0]

    monkeypatch.setattr(_server.time, "monotonic", _now)
    return ticks


@pytest.fixture
def wire_managers(monkeypatch: pytest.MonkeyPatch):
    """Inject fake config / session managers into the module-level getters."""

    def _wire(
        *,
        config_mgr: FakeConfigManager | None = None,
        session_mgr: FakeSessionManager | None = None,
    ) -> tuple[FakeConfigManager, FakeSessionManager]:
        cfg = config_mgr if config_mgr is not None else FakeConfigManager()
        sess = session_mgr if session_mgr is not None else FakeSessionManager()
        monkeypatch.setattr(_server, "_get_config_mgr", lambda: cfg)
        monkeypatch.setattr(_server, "_get_session_mgr", lambda: sess)
        return cfg, sess

    return _wire


@pytest.fixture
def patch_lookups(monkeypatch: pytest.MonkeyPatch):
    """Stub ``_sessions_for`` / ``_configs_for`` directly for tests that don't care about the manager chain."""

    def _patch(sessions: dict[str, list[Any]] | None = None, configs: dict[str, list[Any]] | None = None) -> None:
        sessions = sessions or {}
        configs = configs or {}
        monkeypatch.setattr(_server, "_sessions_for", lambda name: list(sessions.get(name, [])))
        monkeypatch.setattr(_server, "_configs_for", lambda name: list(configs.get(name, [])))

    return _patch
