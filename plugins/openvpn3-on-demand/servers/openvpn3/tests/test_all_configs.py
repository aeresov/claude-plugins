"""Regression tests for the openvpn3 configs-list parser.

Three separate bug reports hit the same `_all_configs` function on three
different plugin versions. These fixtures — captured from real
`openvpn3 configs-list --json` output on openvpn3-linux 23.x — exist to
lock the contract down.
"""

from __future__ import annotations

from unittest.mock import patch

from openvpn3_mcp.server import _all_configs


# Real output shape: the outer dict is keyed by D-Bus path. The path does
# NOT appear as a field inside each value.
REAL_JSON_WITH_DUPLICATES = """
{
  "/net/openvpn/v3/configuration/40a363c8abc": {"name": "aiosws-vpn", "valid": true, "persistent": true},
  "/net/openvpn/v3/configuration/adf18bb4def": {"name": "aiosws-vpn", "valid": true, "persistent": true},
  "/net/openvpn/v3/configuration/ghi00000009": {"name": "other-vpn",  "valid": true, "persistent": true}
}
"""

REAL_JSON_EMPTY = "{}\n"

TEXT_OUTPUT_WITH_ONE_CONFIG = """
 -----------------------------------------------------------------------------
  Configuration path: /net/openvpn/v3/configuration/aaaa
             Imported: Wed Jan 01 00:00:00 2025
             Name:    text-only-vpn
 -----------------------------------------------------------------------------
"""


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(*, json_stdout: str = "", json_rc: int = 0,
              text_stdout: str = "", text_rc: int = 0):
    """Build a `_run` replacement that routes `--json` vs plain `configs-list`."""

    def inner(*args, **_kwargs):
        if args[-1] == "--json":
            return _FakeCompletedProcess(json_rc, json_stdout)
        return _FakeCompletedProcess(text_rc, text_stdout)

    return inner


def test_json_with_path_as_outer_dict_key():
    """The actual openvpn3 output: dict keyed by D-Bus path. Was silently returning []."""
    with patch(
        "openvpn3_mcp.server._run",
        _fake_run(json_stdout=REAL_JSON_WITH_DUPLICATES),
    ):
        configs = _all_configs()

    assert len(configs) == 3, configs
    assert {c["path"] for c in configs} == {
        "/net/openvpn/v3/configuration/40a363c8abc",
        "/net/openvpn/v3/configuration/adf18bb4def",
        "/net/openvpn/v3/configuration/ghi00000009",
    }
    assert sorted(c["name"] for c in configs) == ["aiosws-vpn", "aiosws-vpn", "other-vpn"]


def test_json_empty_dict_is_empty_list():
    with patch(
        "openvpn3_mcp.server._run",
        _fake_run(json_stdout=REAL_JSON_EMPTY),
    ):
        assert _all_configs() == []


def test_json_failure_falls_through_to_text_parser():
    """Older openvpn3 builds may not support --json; text fallback must work."""
    with patch(
        "openvpn3_mcp.server._run",
        _fake_run(json_rc=1, text_stdout=TEXT_OUTPUT_WITH_ONE_CONFIG),
    ):
        configs = _all_configs()

    assert configs == [
        {"path": "/net/openvpn/v3/configuration/aaaa", "name": "text-only-vpn"}
    ]


def test_both_paths_failing_returns_empty_list():
    with patch(
        "openvpn3_mcp.server._run",
        _fake_run(json_rc=1, text_rc=1),
    ):
        assert _all_configs() == []


def test_tolerates_hypothetical_list_of_objects_shape():
    """Defensive: if a future openvpn3 emits a list with inline `path`, handle it."""
    future_shape = (
        '[{"path": "/net/openvpn/v3/configuration/xxx", "name": "future-vpn"}]'
    )
    with patch(
        "openvpn3_mcp.server._run",
        _fake_run(json_stdout=future_shape),
    ):
        configs = _all_configs()

    assert configs == [
        {"path": "/net/openvpn/v3/configuration/xxx", "name": "future-vpn"}
    ]


def test_config_with_missing_name_gets_empty_string():
    """Anonymous/malformed configs don't crash the parser."""
    weird = (
        '{"/net/openvpn/v3/configuration/noname": {"valid": true}}'
    )
    with patch(
        "openvpn3_mcp.server._run",
        _fake_run(json_stdout=weird),
    ):
        configs = _all_configs()

    assert configs == [
        {"path": "/net/openvpn/v3/configuration/noname", "name": ""}
    ]
