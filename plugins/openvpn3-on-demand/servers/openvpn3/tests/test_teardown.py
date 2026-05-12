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


# ---------- main() integration -------------------------------------------


def _write_settings(tmp_path: Path, frontmatter: str) -> Path:
    p = tmp_path / "openvpn3-on-demand.local.md"
    p.write_text(f"---\n{frontmatter}\n---\n\n# notes for humans\n")
    return p


def test_main_noop_when_state_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(teardown, "STATE_FILE", tmp_path / "absent.md")
    with patch.object(teardown, "_disconnect_via_dbus") as disc:
        assert teardown.main() == 0
    disc.assert_not_called()


def test_main_noop_when_both_fields_set(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "profile_name: a\novpn_provision_cmd: cat x")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    with patch.object(teardown, "_disconnect_via_dbus") as disc, patch.object(
        teardown, "_remove_config_via_dbus"
    ) as rem:
        assert teardown.main() == 0
    disc.assert_not_called()
    rem.assert_not_called()


def test_main_byo_disconnects_named_profile_no_config_removal(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "profile_name: my-vpn")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    with patch.object(teardown, "_disconnect_via_dbus", return_value=True) as disc, patch.object(
        teardown, "_remove_config_via_dbus"
    ) as rem:
        assert teardown.main() == 0
    disc.assert_called_once_with("my-vpn")
    rem.assert_not_called()


def test_main_byo_runs_post_disconnect_when_disconnected(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "profile_name: my-vpn\npost_disconnect_cmd: echo bye")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    with patch.object(teardown, "_disconnect_via_dbus", return_value=True), patch.object(
        teardown, "run_post_disconnect"
    ) as post:
        assert teardown.main() == 0
    post.assert_called_once_with("echo bye")


def test_main_byo_skips_post_disconnect_when_nothing_disconnected(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "profile_name: my-vpn\npost_disconnect_cmd: echo bye")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    with patch.object(teardown, "_disconnect_via_dbus", return_value=False), patch.object(
        teardown, "run_post_disconnect"
    ) as post:
        assert teardown.main() == 0
    post.assert_not_called()


def test_main_ephemeral_uses_session_id_from_env(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "ovpn_provision_cmd: cat x")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-sid")
    with patch.object(teardown, "_disconnect_via_dbus", return_value=True) as disc, patch.object(
        teardown, "_remove_config_via_dbus"
    ) as rem:
        assert teardown.main() == 0
    disc.assert_called_once_with("ovpn3-od-env-sid")
    rem.assert_called_once_with("ovpn3-od-env-sid")


def test_main_ephemeral_falls_back_to_stdin_session_id(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "ovpn_provision_cmd: cat x")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id": "stdin-sid"}'))
    with patch.object(teardown, "_disconnect_via_dbus", return_value=True) as disc, patch.object(
        teardown, "_remove_config_via_dbus"
    ) as rem:
        assert teardown.main() == 0
    disc.assert_called_once_with("ovpn3-od-stdin-sid")
    rem.assert_called_once_with("ovpn3-od-stdin-sid")


def test_main_ephemeral_noop_without_session_id(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "ovpn_provision_cmd: cat x")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # empty → no session id
    with patch.object(teardown, "_disconnect_via_dbus") as disc, patch.object(
        teardown, "_remove_config_via_dbus"
    ) as rem:
        assert teardown.main() == 0
    disc.assert_not_called()
    rem.assert_not_called()


def test_main_ephemeral_removes_config_even_when_nothing_disconnected(tmp_path, monkeypatch):
    sf = _write_settings(tmp_path, "ovpn_provision_cmd: cat x")
    monkeypatch.setattr(teardown, "STATE_FILE", sf)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid")
    with patch.object(teardown, "_disconnect_via_dbus", return_value=False) as disc, patch.object(
        teardown, "_remove_config_via_dbus"
    ) as rem, patch.object(teardown, "run_post_disconnect") as post:
        assert teardown.main() == 0
    disc.assert_called_once_with("ovpn3-od-sid")
    rem.assert_called_once_with("ovpn3-od-sid")
    post.assert_not_called()
