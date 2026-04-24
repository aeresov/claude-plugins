# SPDX-License-Identifier: AGPL-3.0-only
"""Stub out the system-provided ``dbus`` and ``openvpn3`` modules for tests.

CI runs on a stock Ubuntu GitHub runner that doesn't ship
``openvpn3-client`` or ``python3-dbus``. We never need real D-Bus in tests —
every tool's I/O is exercised via ``unittest.mock`` — so we inject stub
modules into ``sys.modules`` before the server module (which does
``import dbus`` / ``import openvpn3`` at top-level) gets imported.

Only ``dbus.exceptions.DBusException`` has to be a *real* exception class,
because the server uses it in ``except`` clauses.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _install_stubs() -> None:
    if "dbus" in sys.modules and not isinstance(sys.modules["dbus"], types.ModuleType):
        return

    if "dbus" not in sys.modules:
        dbus_mod = MagicMock(name="dbus")
        dbus_exc = types.ModuleType("dbus.exceptions")

        class _DBusException(Exception):
            def __init__(self, message: str = ""):
                super().__init__(message)
                self._message = message

            def get_dbus_message(self) -> str:
                return self._message or str(self)

        dbus_exc.DBusException = _DBusException
        dbus_mod.exceptions = dbus_exc
        sys.modules["dbus"] = dbus_mod
        sys.modules["dbus.exceptions"] = dbus_exc

    if "openvpn3" not in sys.modules:
        sys.modules["openvpn3"] = MagicMock(name="openvpn3")


_install_stubs()
