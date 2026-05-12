# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for the openvpn3-on-demand teardown hook (hooks/scripts/teardown.py).

The hook lives outside this uv project, so we add its directory to sys.path and
import it directly. Its D-Bus helpers import dbus/openvpn3 lazily and are mocked
here, so these tests run on a stock CI runner (the conftest.py stubs in this
directory cover anything that does touch dbus).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hooks" / "scripts"))
import teardown  # noqa: E402


# ---------- _resolve_target (pure) ---------------------------------------


@pytest.mark.parametrize(
    "fields, session_id, expected",
    [
        ({"profile_name": "my-vpn"}, None, ("my-vpn", False)),
        ({"profile_name": "my-vpn"}, "sid", ("my-vpn", False)),
        ({"ovpn_provision_cmd": "cat x"}, "sid-123", ("ovpn3-od-sid-123", True)),
        ({"ovpn_provision_cmd": "cat x"}, None, (None, False)),
        ({"profile_name": "a", "ovpn_provision_cmd": "cat x"}, "sid", (None, False)),
        ({}, "sid", (None, False)),
    ],
)
def test_resolve_target(fields, session_id, expected):
    assert teardown._resolve_target(fields, session_id) == expected


# ---------- _session_id_from_stdin ---------------------------------------


def test_session_id_from_stdin_valid(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id": "abc-123", "cwd": "/x"}'))
    assert teardown._session_id_from_stdin() == "abc-123"


def test_session_id_from_stdin_tty_returns_none(monkeypatch):
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _Tty('{"session_id": "should-not-be-read"}'))
    assert teardown._session_id_from_stdin() is None


def test_session_id_from_stdin_bad_json(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
    assert teardown._session_id_from_stdin() is None


def test_session_id_from_stdin_missing_key(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"cwd": "/x"}'))
    assert teardown._session_id_from_stdin() is None


def test_session_id_from_stdin_empty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert teardown._session_id_from_stdin() is None
