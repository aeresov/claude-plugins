# SPDX-License-Identifier: AGPL-3.0-only
"""Fake dbus/openvpn3 surface used by conftest + tests.

Kept here (not inline in conftest) so test files can import the helper classes
to build sessions/configs without touching real D-Bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Wrapped:
    """Marker for what ``_wrap_override_value`` boxed a value as. Tests assert ``kind``."""

    kind: str
    value: Any


class FakeDBusException(Exception):
    def __init__(self, msg: str = "") -> None:
        super().__init__(msg)
        self._msg = msg

    def get_dbus_message(self) -> str:
        return self._msg


@dataclass
class FakeSession:
    """Mock of an openvpn3 session object (the one Retrieve / NewTunnel returns)."""

    path: str = "/net/openvpn/v3/sessions/abc"
    properties: dict[str, str] = field(default_factory=lambda: {"config_name": "demo"})
    status: dict[str, Any] | None = None
    raise_on_properties: tuple[str, ...] = ()
    raise_on_status: bool = False
    ready_attempts_until_ok: int = 0
    ready_always_fails: bool = False
    raise_on_connect: bool = False
    raise_on_disconnect: bool = False
    disconnect_error_msg: str = "disconnect failed"

    ready_calls: int = 0
    connect_calls: int = 0
    disconnect_calls: int = 0

    def GetPath(self) -> str:
        return self.path

    def GetProperty(self, key: str) -> str:
        if key in self.raise_on_properties:
            raise FakeDBusException(f"no property {key!r}")
        if key not in self.properties:
            raise FakeDBusException(f"no property {key!r}")
        return self.properties[key]

    def GetStatus(self) -> dict[str, Any]:
        if self.raise_on_status:
            raise FakeDBusException("status unavailable")
        if self.status is not None:
            return self.status
        major = type("E", (), {"name": "CONNECTION"})()
        minor = type("E", (), {"name": "CONN_CONNECTED"})()
        return {"major": major, "minor": minor, "message": "ok"}

    def Ready(self) -> None:
        self.ready_calls += 1
        if self.ready_always_fails or self.ready_calls <= self.ready_attempts_until_ok:
            raise FakeDBusException("backend not ready")

    def Connect(self) -> None:
        self.connect_calls += 1
        if self.raise_on_connect:
            raise FakeDBusException("connect failed")

    def Disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.raise_on_disconnect:
            raise FakeDBusException(self.disconnect_error_msg)


@dataclass
class FakeConfig:
    name: str = "demo"
    raise_on_override: str | None = None
    overrides_set: list[tuple[str, Any]] = field(default_factory=list)
    removed: bool = False

    def SetOverride(self, name: str, value: Any) -> None:
        if self.raise_on_override == name:
            raise FakeDBusException(f"override {name!r} rejected")
        self.overrides_set.append((name, value))

    def Remove(self) -> None:
        self.removed = True


class FakeSessionManager:
    """Stand-in for ``openvpn3.SessionManager``. Wire from tests via ``server._get_session_mgr``."""

    def __init__(
        self,
        *,
        sessions_by_name: dict[str, list[FakeSession]] | None = None,
        all_sessions: list[FakeSession] | None = None,
        new_tunnel_session: FakeSession | None = None,
        raise_on_lookup: bool = False,
        raise_on_fetch: bool = False,
        raise_on_new_tunnel: bool = False,
    ) -> None:
        self.sessions_by_name = sessions_by_name or {}
        self.all_sessions = all_sessions or []
        self.new_tunnel_session = new_tunnel_session
        self.raise_on_lookup = raise_on_lookup
        self.raise_on_fetch = raise_on_fetch
        self.raise_on_new_tunnel = raise_on_new_tunnel
        self.new_tunnel_calls: list[Any] = []

    def LookupConfigName(self, name: str) -> list[str]:
        if self.raise_on_lookup:
            raise FakeDBusException("lookup failed")
        return [f"path-{name}-{i}" for i in range(len(self.sessions_by_name.get(name, [])))]

    def Retrieve(self, path: str) -> FakeSession:
        for name, sessions in self.sessions_by_name.items():
            prefix = f"path-{name}-"
            if path.startswith(prefix):
                return sessions[int(path[len(prefix) :])]
        raise FakeDBusException(f"unknown path {path}")

    def FetchAvailableSessions(self) -> list[FakeSession]:
        if self.raise_on_fetch:
            raise FakeDBusException("fetch failed")
        return list(self.all_sessions)

    def NewTunnel(self, cfg: Any) -> FakeSession:
        self.new_tunnel_calls.append(cfg)
        if self.raise_on_new_tunnel:
            raise FakeDBusException("new tunnel failed")
        if self.new_tunnel_session is None:
            return FakeSession()
        return self.new_tunnel_session


class FakeConfigManager:
    def __init__(
        self,
        *,
        configs_by_name: dict[str, list[FakeConfig]] | None = None,
        raise_on_lookup: bool = False,
        raise_on_import: bool = False,
    ) -> None:
        self.configs_by_name = configs_by_name or {}
        self.raise_on_lookup = raise_on_lookup
        self.raise_on_import = raise_on_import
        self.import_calls: list[dict[str, Any]] = []

    def LookupConfigName(self, name: str) -> list[str]:
        if self.raise_on_lookup:
            raise FakeDBusException("lookup failed")
        return [f"cfgpath-{name}-{i}" for i in range(len(self.configs_by_name.get(name, [])))]

    def Retrieve(self, path: str) -> FakeConfig:
        for name, configs in self.configs_by_name.items():
            prefix = f"cfgpath-{name}-"
            if path.startswith(prefix):
                return configs[int(path[len(prefix) :])]
        raise FakeDBusException(f"unknown path {path}")

    def Import(self, name: str, cfg: str, single_use: bool, persistent: bool) -> None:  # noqa: FBT001
        self.import_calls.append({"name": name, "cfg": cfg, "single_use": single_use, "persistent": persistent})
        if self.raise_on_import:
            raise FakeDBusException("import failed")
