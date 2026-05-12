# openvpn3-on-demand: BYO vs. ephemeral profiles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `profile_name` optional in the `openvpn3-on-demand` per-project settings file by adding a second, mutually-exclusive mode: `ovpn_provision_cmd` (a command whose stdout is an `.ovpn` body) drives a per-turn-disposable, single-use openvpn3 config named `ovpn3-od-<Claude session id>`.

**Architecture:** The MCP server stays name-addressed and stateless — it gains one optional `single_use` flag on `vpn_config_import`. All the new orchestration is policy in `SKILL.md` (prose Claude follows) plus a mode-aware rewrite of the `teardown.py` safety-net hook. The ephemeral config's name is derived from `CLAUDE_CODE_SESSION_ID` (in the Bash env for the skill; in the hook's env or its stdin JSON for `teardown.py`) so the skill and the hook independently agree on it with no state file. Bumps the plugin to **0.5.0**.

**Tech Stack:** Python 3.10+ (MCP server via `uv`, FastMCP; the hook is a stdlib-only script), `pytest` (server tests mock D-Bus; the hook test adds the hook's dir to `sys.path`), openvpn3-linux D-Bus API via the `openvpn3` Python module + `dbus-python`.

---

## Notes before you start

- **Source of truth:** the design spec at `docs/superpowers/specs/2026-05-12-openvpn3-ephemeral-profiles-design.md`. If this plan and the spec ever disagree, the spec wins — flag the discrepancy.
- **Branch:** work on `openvpn3-ephemeral-profiles` (the spec is already committed there) or a worktree off it.
- **Untracked files:** the repo currently has two untracked `CLAUDE.md` files from prior work — `./CLAUDE.md` (repo root) and `plugins/openvpn3-on-demand/CLAUDE.md`. Task 5 modifies the **plugin** one and `git add`s it as part of that commit. **Do not** touch or commit `./CLAUDE.md` — it's unrelated to this feature.
- **All commit commands** in this plan end with the trailer line `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Keep it.
- **Working directory:** start every step from the repo root (`/home/aeresov/workshop/aeresov/claude-plugins`). Steps that need another directory `cd` there explicitly (the `pytest` steps `cd plugins/openvpn3-on-demand/servers/openvpn3` — `cd` back to the repo root before the next step). `git`, `grep`, and `claude plugin validate` commands all run from the repo root.
- **Running tests:** the `pytest` steps assume you've run `uv sync --group dev` once in `plugins/openvpn3-on-demand/servers/openvpn3/`. The repo CI runs exactly `cd plugins/openvpn3-on-demand/servers/openvpn3 && uv sync --group dev --quiet && uv run --quiet pytest -q`.
- **Plugin paths** in this plan are written relative to the repo root. `PLUGIN/` is shorthand for `plugins/openvpn3-on-demand/`.

---

## Task 1: MCP server — `single_use` flag on `vpn_config_import`

Add an optional `single_use: bool = False` parameter. When true, import the config as memory-only (`persistent=False`) and single-use (openvpn3 drops it once a tunnel is started from it). Default behavior and its existing regression test are unchanged.

**Files:**
- Modify: `PLUGIN/servers/openvpn3/openvpn3_mcp/server.py` (the `vpn_config_import` tool, ~lines 281–337)
- Test: `PLUGIN/servers/openvpn3/tests/test_server.py` (add one test near the other `vpn_config_import` tests)

- [ ] **Step 1: Write the failing test**

Append to `PLUGIN/servers/openvpn3/tests/test_server.py`, in the `# ---------- vpn_config_import ----...` section (after `test_vpn_config_import_already_imported`):

```python
def test_vpn_config_import_single_use(tmp_path):
    """single_use=True passes single_use=True, persistent=False to Import and echoes the flag back."""
    ovpn = tmp_path / "eph.ovpn"
    ovpn.write_text(
        "client\ndev tun\n"
        "<ca>\n-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n</ca>\n"
    )
    cfg_mgr = MagicMock()
    cfg_mgr.LookupConfigName.return_value = []
    cfg_mgr.Import.return_value = MagicMock(
        GetPath=MagicMock(return_value="/net/openvpn/v3/configuration/eph")
    )
    with patch.object(server, "_get_config_mgr", return_value=cfg_mgr):
        result = server.vpn_config_import(
            ovpn_path=str(ovpn), profile_name="eph-vpn", single_use=True
        )
    assert result["status"] == "imported"
    assert result["single_use"] is True
    cfg_mgr.Import.assert_called_once()
    args, _ = cfg_mgr.Import.call_args
    name, body, single_use, persistent = args
    assert name == "eph-vpn"
    assert single_use is True
    assert persistent is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv run pytest tests/test_server.py::test_vpn_config_import_single_use -v
```
Expected: FAIL — `vpn_config_import() got an unexpected keyword argument 'single_use'`.

- [ ] **Step 3: Add the parameter**

In `PLUGIN/servers/openvpn3/openvpn3_mcp/server.py`, change the `vpn_config_import` signature and docstring, the `Import(...)` call, and the success return.

Signature + docstring — replace:

```python
@mcp.tool()
def vpn_config_import(ovpn_path: str, profile_name: str) -> dict:
    """Import a .ovpn file as a persistent OpenVPN3 config. Idempotent: returns early if a config with this name already exists.

    Args:
        ovpn_path: Path to the .ovpn file to import (~ expansion supported).
        profile_name: Name to register the imported config under.
    """
```

with:

```python
@mcp.tool()
def vpn_config_import(
    ovpn_path: str, profile_name: str, single_use: bool = False
) -> dict:
    """Import a .ovpn file as a named OpenVPN3 config. Idempotent: returns early if a config with this name already exists.

    Args:
        ovpn_path: Path to the .ovpn file to import (~ expansion supported).
        profile_name: Name to register the imported config under.
        single_use: If True, register an ephemeral config — memory-only (not written to
            openvpn3's on-disk config store) and dropped by openvpn3 once a tunnel is started
            from it. Use for throwaway profiles. Default False (persistent, like the openvpn3
            `config-import --persistent` CLI does).
    """
```

The `Import(...)` call — replace:

```python
        cfg = _get_config_mgr().Import(
            profile_name,
            cfg_str,
            False,  # single_use
            True,   # persistent
        )
```

with:

```python
        cfg = _get_config_mgr().Import(
            profile_name,
            cfg_str,
            single_use,         # single_use
            not single_use,     # persistent — ephemeral configs aren't written to disk
        )
```

The success return — replace:

```python
    return {
        "status": "imported",
        "profile_name": profile_name,
        "ovpn_path": str(path),
        "config_path": str(cfg.GetPath()),
    }
```

with:

```python
    return {
        "status": "imported",
        "profile_name": profile_name,
        "ovpn_path": str(path),
        "config_path": str(cfg.GetPath()),
        "single_use": single_use,
    }
```

Leave the big `# Hand the raw .ovpn contents ...` comment block above the `Import` call untouched.

> If a live openvpn3 backend later rejects `single_use=True, persistent=False` (test this manually when validating against a real machine — out of scope for this plan), the fallback is `persistent=True` regardless of `single_use`; update the comment accordingly. The defensive `vpn_config_remove` in the skill/hook covers cleanup either way.

- [ ] **Step 4: Run the new test and the full suite**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv run pytest tests/test_server.py::test_vpn_config_import_single_use -v
uv run pytest -q
```
Expected: the new test PASSES; the full suite is all green (the existing `test_vpn_config_import_passes_raw_ovpn_contents` still passes — it asserts the *default* call passes `single_use=False, persistent=True`, which is `not single_use` with `single_use=False`).

- [ ] **Step 5: Commit**

```bash
git add plugins/openvpn3-on-demand/servers/openvpn3/openvpn3_mcp/server.py \
        plugins/openvpn3-on-demand/servers/openvpn3/tests/test_server.py
git commit -m "$(cat <<'EOF'
Add single_use flag to vpn_config_import

Ephemeral profiles import memory-only (persistent=False) and single-use
so openvpn3 drops the config once a tunnel is started from it. Default
unchanged (persistent, not single-use).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: teardown hook — pure helpers (`_session_id_from_stdin`, `_resolve_target`)

Add the two new pure-ish helpers and a new test file. `main()` is **not** changed in this task — the helpers are added and tested in isolation; Task 3 wires them in.

**Files:**
- Modify: `PLUGIN/hooks/scripts/teardown.py` (add `import json`; add two functions)
- Create: `PLUGIN/servers/openvpn3/tests/test_teardown.py`

- [ ] **Step 1: Create the test file with tests for the two helpers**

Create `PLUGIN/servers/openvpn3/tests/test_teardown.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv run pytest tests/test_teardown.py -v
```
Expected: FAIL — `AttributeError: module 'teardown' has no attribute '_resolve_target'` (and `_session_id_from_stdin`).

- [ ] **Step 3: Add the helpers to `teardown.py`**

In `PLUGIN/hooks/scripts/teardown.py`:

(a) Add `import json` to the imports block. After this change the imports read:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
```

(b) Add these two functions just **above** `def main() -> int:` (after `run_post_disconnect`):

```python
def _session_id_from_stdin() -> str | None:
    """Best-effort: the Claude session id from the hook's JSON stdin payload.

    Stop / SessionEnd hooks receive a JSON object on stdin with a ``session_id``
    field. We only consult stdin when ``CLAUDE_CODE_SESSION_ID`` wasn't in the
    environment. If stdin is a tty (someone ran this script by hand) or isn't
    valid JSON, return None instead of blocking or raising — this is a safety
    net, not a hard dependency.
    """
    try:
        if sys.stdin.isatty():
            return None
        data = json.load(sys.stdin)
    except Exception:
        return None
    if isinstance(data, dict):
        sid = data.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
    return None


def _resolve_target(
    fields: dict[str, str], session_id: str | None
) -> tuple[str | None, bool]:
    """Map settings-file frontmatter to (config name to tear down, also remove its config?).

    - ``profile_name`` set, no ``ovpn_provision_cmd`` → BYO: disconnect that named
      session; leave its config alone (it's the user's).
    - ``ovpn_provision_cmd`` set, no ``profile_name`` → ephemeral: disconnect and
      remove the config for ``ovpn3-od-<session_id>``; needs a session id.
    - both set → misconfigured; the skill won't have started anything → no-op.
    - neither, or ephemeral with no session id → no-op.

    Every no-op case returns ``(None, False)``.
    """
    profile = fields.get("profile_name")
    provision = fields.get("ovpn_provision_cmd")
    if profile and provision:
        return None, False
    if profile:
        return profile, False
    if provision:
        if not session_id:
            return None, False
        return f"ovpn3-od-{session_id}", True
    return None, False
```

- [ ] **Step 4: Run the tests**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv run pytest tests/test_teardown.py -v
uv run pytest -q
```
Expected: `tests/test_teardown.py` all PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add plugins/openvpn3-on-demand/hooks/scripts/teardown.py \
        plugins/openvpn3-on-demand/servers/openvpn3/tests/test_teardown.py
git commit -m "$(cat <<'EOF'
teardown hook: add _resolve_target and _session_id_from_stdin helpers

Pure helpers for the upcoming mode-aware teardown: _resolve_target maps
settings frontmatter to (config name, also-remove-config), and
_session_id_from_stdin pulls the Claude session id off the hook's JSON
stdin as a fallback. Not yet wired into main().

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: teardown hook — mode-aware `main()` + `_remove_config_via_dbus`

Rewrite `main()` to branch on mode (BYO vs. ephemeral), resolving the ephemeral name from `CLAUDE_CODE_SESSION_ID` (env, else stdin). Add a best-effort `_remove_config_via_dbus` mirroring the existing `_disconnect_via_dbus`.

**Files:**
- Modify: `PLUGIN/hooks/scripts/teardown.py` (add `_remove_config_via_dbus`; rewrite `main()`)
- Modify: `PLUGIN/servers/openvpn3/tests/test_teardown.py` (add `main()` integration tests)

- [ ] **Step 1: Write the failing `main()` integration tests**

Append to `PLUGIN/servers/openvpn3/tests/test_teardown.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv run pytest tests/test_teardown.py -k main -v
```
Expected: FAILs — the current `main()` only knows `profile_name`, calls no `_remove_config_via_dbus` (which doesn't exist yet), and doesn't compute `ovpn3-od-<id>`. E.g. `test_main_ephemeral_uses_session_id_from_env` fails because `_disconnect_via_dbus` is never called; `test_main_byo_disconnects_named_profile_no_config_removal` fails on `AttributeError: module 'teardown' has no attribute '_remove_config_via_dbus'` raised inside the `patch.object` for `_remove_config_via_dbus`.

- [ ] **Step 3: Add `_remove_config_via_dbus` to `teardown.py`**

Add this function in `PLUGIN/hooks/scripts/teardown.py` immediately **after** `_disconnect_via_dbus` (before `run_post_disconnect`):

```python
def _remove_config_via_dbus(profile: str) -> None:
    """Best-effort: remove every openvpn3 config registered under ``profile``.

    Mirrors ``_disconnect_via_dbus`` but against the configuration manager.
    Ephemeral configs are imported single-use and openvpn3 normally drops them
    once a tunnel starts, so this is usually a no-op; it covers the case where a
    turn imported a config but its NewTunnel threw before consuming it. Swallows
    everything — never raises to the hook runner.
    """
    try:
        import dbus  # type: ignore[import-not-found]
        import openvpn3  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        mgr = openvpn3.ConfigurationManager(dbus.SystemBus())
        paths = mgr.LookupConfigName(profile)
    except (dbus.exceptions.DBusException, RuntimeError):
        return
    for p in paths or []:
        try:
            mgr.Retrieve(p).Remove()
        except (dbus.exceptions.DBusException, RuntimeError):
            continue
```

- [ ] **Step 4: Rewrite `main()`**

In `PLUGIN/hooks/scripts/teardown.py`, replace the entire current `main()` function:

```python
def main() -> int:
    if not STATE_FILE.is_file():
        return 0
    fields = _frontmatter_fields(STATE_FILE, "profile_name", "post_disconnect_cmd")
    profile = fields.get("profile_name")
    if not profile:
        return 0
    if _disconnect_via_dbus(profile):
        post_cmd = fields.get("post_disconnect_cmd")
        if post_cmd:
            run_post_disconnect(post_cmd)
    return 0
```

with:

```python
def main() -> int:
    if not STATE_FILE.is_file():
        return 0
    fields = _frontmatter_fields(
        STATE_FILE, "profile_name", "ovpn_provision_cmd", "post_disconnect_cmd"
    )
    # Only consult the environment / stdin for a session id when we'd actually
    # use one (ephemeral mode) — otherwise a manual `teardown.py` run on a tty
    # would block in json.load(sys.stdin).
    profile = fields.get("profile_name")
    provision = fields.get("ovpn_provision_cmd")
    session_id: str | None = None
    if provision and not profile:
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or _session_id_from_stdin()
    target, also_remove = _resolve_target(fields, session_id)
    if not target:
        return 0
    disconnected = _disconnect_via_dbus(target)
    if also_remove:
        _remove_config_via_dbus(target)
    if disconnected:
        post_cmd = fields.get("post_disconnect_cmd")
        if post_cmd:
            run_post_disconnect(post_cmd)
    return 0
```

Also update the module docstring at the top of `teardown.py` — replace:

```python
"""Safety-net teardown for openvpn3-on-demand.

Fires on Stop and SessionEnd. Reads profile_name from the project's
.claude/openvpn3-on-demand.local.md (YAML frontmatter) and disconnects
that single profile iff it's currently active. Silent no-op otherwise;
never raises to the hook runner.
```

with:

```python
"""Safety-net teardown for openvpn3-on-demand.

Fires on Stop and SessionEnd. Reads the project's
.claude/openvpn3-on-demand.local.md (YAML frontmatter) to learn which
mode the project uses, then disconnects that single profile iff it's
active. BYO mode (profile_name): disconnect that named session, leave
its config alone. Ephemeral mode (ovpn_provision_cmd): disconnect — and
remove the config for — ``ovpn3-od-<CLAUDE_CODE_SESSION_ID>`` (the id
comes from the environment, or from the hook's JSON stdin as a
fallback). Misconfigured (both fields, or neither) or no session id:
silent no-op. Never raises to the hook runner.
```

(Leave the rest of the docstring — the paragraphs about D-Bus and scoping — as is.)

- [ ] **Step 5: Run the teardown tests, then the full suite**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv run pytest tests/test_teardown.py -v
uv run pytest -q
```
Expected: all `tests/test_teardown.py` PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add plugins/openvpn3-on-demand/hooks/scripts/teardown.py \
        plugins/openvpn3-on-demand/servers/openvpn3/tests/test_teardown.py
git commit -m "$(cat <<'EOF'
teardown hook: mode-aware teardown (BYO vs ephemeral)

main() now branches on the settings file: profile_name => disconnect
that named session (no config removal); ovpn_provision_cmd => disconnect
and remove the config for ovpn3-od-<CLAUDE_CODE_SESSION_ID>, resolving
the id from the env or the hook's stdin JSON. Both fields, or neither,
or no session id => silent no-op.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: rewrite the skill (`SKILL.md` + `example-local-settings.md`)

Replace both files with mode-aware content matching the spec. No code/tests — prose. The skill is what makes the plugin *work*, so the content below is complete; use it verbatim.

**Files:**
- Modify (overwrite): `PLUGIN/skills/vpn-on-demand/SKILL.md`
- Modify (overwrite): `PLUGIN/skills/vpn-on-demand/references/example-local-settings.md`

- [ ] **Step 1: Overwrite `SKILL.md`**

Write `PLUGIN/skills/vpn-on-demand/SKILL.md` with exactly:

````markdown
---
name: vpn-on-demand
description: Connect the project's OpenVPN3 tunnel before commands that touch private network resources — RDS/ElastiCache/MemoryDB hosts, internal hostnames, private kubectl contexts, RFC1918 targets of remote-access verbs, plus any trigger_patterns declared in .claude/openvpn3-on-demand.local.md — and disconnect at task end.
---

# VPN On Demand

Bring up the project's OpenVPN3 tunnel before running any command that needs it, and tear it down when the task is done. The plugin ships an `openvpn3` MCP server with the tools below; this skill is the policy that decides *when* to call them.

The project picks one of two modes in `.claude/openvpn3-on-demand.local.md`:

- **BYO profile** — `profile_name` names an openvpn3 config the user has already imported. The skill only starts/stops sessions for it; the config is the user's and is never created or removed by the plugin.
- **Ephemeral profile** — `ovpn_provision_cmd` is a command whose stdout is an `.ovpn` file body. The skill generates a fresh, single-use config from it whenever the tunnel is needed, under an internal name the user never sees; openvpn3 drops the config once the tunnel starts.

## Preflight: is this plugin active for this project, and in which mode?

1. Check whether `.claude/openvpn3-on-demand.local.md` exists in the project root. If not, **stop** — do nothing, call no `vpn_*` tool, handle the user's request normally.
2. If it exists, read its YAML frontmatter. Relevant fields:
   - `profile_name` — selects BYO mode.
   - `ovpn_provision_cmd` — selects ephemeral mode.
   - `trigger_patterns` — optional list of regex strings; extends the built-in triggers below. (Both modes.)
   - `post_connect_cmd` — optional shell command run after a fresh `vpn_connect`. (Both modes.)
   - `post_disconnect_cmd` — optional shell command run after a fresh `vpn_disconnect`. (Both modes.)
3. **Validate the mode.** Exactly one of `profile_name` / `ovpn_provision_cmd` must be present and non-empty:
   - Neither → tell the user the settings file must declare either `profile_name` (an already-imported config) or `ovpn_provision_cmd` (an ephemeral one).
   - Both → tell the user the two fields are mutually exclusive — `profile_name` for an existing config, `ovpn_provision_cmd` for an ephemeral one.
   In either error case: surface the message, call **no** `vpn_*` tool, and proceed with the user's request as normal (their command may fail if it needed the tunnel — that's the misconfiguration's fault, not something to paper over).
4. **Host DNS integration check** (once per session, before the first `vpn_connect`, in either mode).

   Run `test -f /var/lib/openvpn3/netcfg.json` (no sudo needed — world-readable when present). If the file does **not** exist, stop and tell the user to run these once per machine:

   ```bash
   sudo openvpn3-admin init-config --write-configs --force
   sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
   sudo killall -INT openvpn3-service-netcfg
   ```

   (Verify with `sudo openvpn3-admin netcfg-service --config-show` — output should include `Systemd-resolved in use: Yes`.)

   Why it matters: without netcfg initialized the tunnel comes up and TCP to private IPs works, but `systemd-resolved` never receives the pushed DNS servers, so `tun0` shows `Current Scopes: none` and hostname resolution for `*.rds.amazonaws.com` / `*.cache.amazonaws.com` / other private-zone endpoints fails silently with NXDOMAIN. Skip this check only if (a) the user has confirmed the host is set up, or (b) the host is non-systemd (Alpine, minimal Debian without systemd) — on those, hostname access over the tunnel needs DNS glue that is out of scope for this plugin.

## When to activate the VPN

Call the connect step (per the relevant mode's flow below) before executing a command whose destination is a private network resource. Use this matrix:

**Activate** when the command targets:

- Hosts ending in `.rds.amazonaws.com`, `.elasticache.amazonaws.com`, `.memorydb.amazonaws.com`, `.redshift.amazonaws.com`, or `.docdb.amazonaws.com`.
- Hosts in the RFC1918 ranges `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` **only** when targeted by a remote-access verb (`ssh`, `kubectl`, `mysql`, `psql`, `redis-cli`, `curl`/`wget` to a non-loopback URL, etc.). A bare RFC1918 address on its own is not enough — local Docker networks live in this range too.
- Hostnames ending in `.internal`, `.corp`, `.private`, `.vpc`, or anything documented as internal-only in the project's CLAUDE.md / README.
- `aws` CLI calls against prod accounts for services that talk to private endpoints (RDS, ElastiCache, MemoryDB, Secrets Manager, SSM Parameter Store, ECR inside a VPC, Lambda in a VPC).
- `kubectl` / `helm` against a cluster whose API endpoint is private.
- `ssh` to hosts without a public IP.
- Any command matching one of the `trigger_patterns` regexes in the settings file. Patterns there extend the defaults — they do not replace them.

**Do NOT activate** for:

- Read/Edit/Write/Glob/Grep on local files.
- Commands hitting obviously public endpoints: `github.com`, `pypi.org`, `npmjs.com`, `docker.io`, public S3 over `https://...s3.amazonaws.com` without a VPC-endpoint requirement, public REST APIs.
- Reading docs, running tests that don't hit the network, local builds.
- Local Docker traffic: the `docker0` bridge (`172.17.0.0/16`), `docker compose` project networks, anything on `localhost` / `127.0.0.1` / `::1`.
- `.local` / mDNS / Bonjour hostnames — LAN service discovery, not VPN territory.

When uncertain, check `trigger_patterns` and the project's CLAUDE.md. If still uncertain, ask the user rather than speculating.

## Core flow — BYO mode (`profile_name`)

Given a matching command:

1. **Connect.** `vpn_connect(profile_name=<value>)`. Idempotent — `already_connected` returns immediately.
2. **If connect errors that the config is unknown** (message mentions "no openvpn3 config named" / "import it first" / similar), **stop**: tell the user to import the profile once, e.g.
   `openvpn3 config-import --config /path/to/file.ovpn --name <profile_name> --persistent`.
   Do **not** run any provisioning command in this mode.
3. **Post-connect hook (fresh connects only).** If `vpn_connect` returned `status: connected` (not `already_connected`) and `post_connect_cmd` is set, run it via Bash. Typical uses: warming a DNS cache, probing a VPC endpoint, opening an ssh control master. A non-zero exit is surfaced but **not** fatal — do not tear down on post-connect failure. Skip when the tunnel was already up.
4. **Run the user's command.** Reuse the tunnel for later VPN-gated commands in the same task.
5. **Disconnect at end of task.** When the task is complete and no later step needs the VPN, `vpn_disconnect(profile_name=<value>)`. Idempotent — `not_connected` is fine.
6. **Post-disconnect hook (fresh disconnects only).** If `vpn_disconnect` returned `status: disconnected` (not `not_connected`) and `post_disconnect_cmd` is set, run it via Bash. Failures here are informational. The Stop/SessionEnd safety-net hook also runs `post_disconnect_cmd` (5 s timeout, silent failure) whenever it actually disconnects the session.

Connect once, keep the tunnel up across the VPN-gated commands in a task, disconnect at the end. Do not disconnect between two VPN-gated commands in the same task.

## Core flow — ephemeral mode (`ovpn_provision_cmd`)

The profile name is internal: `N = "ovpn3-od-" + $CLAUDE_CODE_SESSION_ID`. Read `CLAUDE_CODE_SESSION_ID` from the environment. If it is unset or empty, **stop**: tell the user ephemeral mode needs `CLAUDE_CODE_SESSION_ID` (their Claude Code may be too old) and handle their request without VPN — do **not** invent a name.

Given a matching command:

1. **Compute `N`** as above.
2. **Try connecting first.** `vpn_connect(profile_name=N)`.
   - `already_connected` → an earlier VPN-gated command this turn already brought it up. Skip to step 5.
   - `connected` → a config under `N` was still around; it's now consumed. Run the post-connect hook (step 4) and skip to step 5.
   - `error` (no session, no config) → provision, step 3.
3. **Provision → import → connect:**
   1. `vpn_config_remove(profile_name=N)` — defensive cleanup of a stale config left under `N` by an earlier turn this session whose `NewTunnel` threw before consuming it. `already_removed` is the normal case; an `error` here isn't fatal.
   2. Create a temp file: `tmp="$(mktemp --suffix=.ovpn)"`.
   3. Run `ovpn_provision_cmd` with its **stdout redirected into `$tmp`** — e.g. `{ <the ovpn_provision_cmd value, verbatim> ; } > "$tmp"`. The requirement (not the exact mechanism): the command's standard output lands in `$tmp`, and its stdout bytes do **not** appear in your output / the conversation transcript; the command's stderr may (it's the command's diagnostics).
   4. If the command exited non-zero, or `$tmp` is empty → tell the user provisioning failed (show its stderr), `rm -f "$tmp"`, and **stop** — do not connect.
   5. `vpn_config_import(ovpn_path=<path to $tmp>, profile_name=N, single_use=True)`. On `status: error`, surface the `message`, `rm -f "$tmp"`, stop.
   6. `rm -f "$tmp"` — the body is now inside openvpn3; the file is no longer needed.
   7. `vpn_connect(profile_name=N)`. On `status: connected`, run the post-connect hook (step 4). On `status: error`, surface the `message` and stop.
4. **Post-connect hook (fresh connects only).** If `post_connect_cmd` is set, run it via Bash. Non-zero exit is surfaced but not fatal; do not tear down.
5. **Run the user's command.** Reuse the tunnel for later VPN-gated commands in the same task.
6. **Disconnect at end of task.** `vpn_disconnect(profile_name=N)`; on a fresh disconnect, run `post_disconnect_cmd` if set. The Stop/SessionEnd safety-net hook disconnects `N` and removes its config as a backstop, and runs `post_disconnect_cmd` (5 s timeout, silent) if it disconnects something.

`ovpn_provision_cmd` runs on every turn that touches the VPN — that's by design (the profile is per-turn-disposable). Don't try to cache it across turns.

## Refreshing the profile

- **BYO mode:** the imported config is the user's. If they need to refresh it (rotated credentials, env switch), they re-import it themselves (`openvpn3 config-import --name <profile_name> --persistent --config …`); if asked, you can `vpn_disconnect(profile_name)` first so the re-import isn't blocked by an active session. The skill never auto-removes a BYO config.
- **Ephemeral mode:** nothing to refresh — every VPN-gated turn re-runs `ovpn_provision_cmd` and re-imports, so the config is always fresh. If the user edits `ovpn_provision_cmd`, the next VPN-gated turn picks it up automatically.

## Interaction with the teardown hook

The plugin ships Stop and SessionEnd hooks that disconnect the configured profile as a safety net for when the model forgot the "disconnect at end of task" step — BYO mode: `profile_name`; ephemeral mode: `ovpn3-od-<CLAUDE_CODE_SESSION_ID>` (and it also removes that config). Don't lean on them:

- Stop also fires between turns in a long conversation; letting the hook do the disconnect means every follow-up turn pays a reconnect (and, in ephemeral mode, a re-provision).
- Explicit disconnect produces cleaner transcripts (the user sees the intent).

## Tool reference

The MCP server exposes these under the `openvpn3` prefix; all return a dict with a `status` field, and `status: "error"` is a hard failure — surface the `message`, don't silently retry:

- `vpn_status()` — list active sessions (`{session_count, sessions: [...]}`). Useful for confirming state or debugging.
- `vpn_connect(profile_name)` — start a session. Idempotent (`already_connected`).
- `vpn_disconnect(profile_name)` — stop a session. Idempotent (`not_connected`). `profile_name` is required; the server won't disconnect arbitrary sessions.
- `vpn_config_import(ovpn_path, profile_name, single_use=False)` — register an `.ovpn` file as a named config. Idempotent (`already_imported`). Pass `single_use=True` for ephemeral profiles: the config is memory-only and openvpn3 drops it once a tunnel is started from it.
- `vpn_config_remove(profile_name)` — drop an imported config. Idempotent (`already_removed`). Requires the session disconnected first.

## Configuration file

Per-project settings live in `.claude/openvpn3-on-demand.local.md` (git-ignored). Frontmatter:

| Field | Mode | Required | Purpose |
|---|---|---|---|
| `profile_name` | BYO | one-of | Name of an openvpn3 config the user already imported (`openvpn3 config-import --persistent`). The plugin only starts/stops it. |
| `ovpn_provision_cmd` | ephemeral | one-of | Shell command whose **stdout is the `.ovpn` body** (e.g. `vault read -field=config secret/vpn`, `aws s3 cp s3://…/vpn.ovpn -`, `cat secrets/vpn.ovpn`). Re-run every VPN-gated turn. |
| `trigger_patterns` | both | no | Extra regex patterns treated as VPN-requiring, on top of the built-in defaults. |
| `post_connect_cmd` | both | no | Shell command run after a fresh `vpn_connect` (not on `already_connected`). DNS warming, endpoint probes, ssh control masters. Non-fatal on failure. |
| `post_disconnect_cmd` | both | no | Shell command run after a fresh `vpn_disconnect` (not on `not_connected`). DNS/route cleanup, closing port-forwards. Also run by the Stop/SessionEnd hook when it disconnects (5 s timeout, silent failure) — keep it quick and idempotent. |

Exactly one of `profile_name` / `ovpn_provision_cmd` — setting both, or neither, is a configuration error (see Preflight). See `references/example-local-settings.md` for full commented templates.

## Failure modes and how to handle them

- **Misconfigured mode** (both, or neither, of `profile_name` / `ovpn_provision_cmd`). Surface the config error to the user; call no `vpn_*` tool; proceed without VPN.
- **`CLAUDE_CODE_SESSION_ID` unset (ephemeral mode).** Tell the user; proceed without VPN; do not guess a name.
- **`ovpn_provision_cmd` fails or emits nothing.** Surface its stderr; `rm -f` the temp file; do not connect.
- **openvpn3 / dbus-python not installed.** All tools return `{"status": "error", "message": "openvpn3 Python module or dbus-python is not available. ..."}`. Tell the user to install the `openvpn3-client` and `python3-dbus` system packages; stop.
- **Connect fails with an auth error, or `"Backend not ready (likely needs credentials embedded in the profile)"`.** The MCP server is non-interactive — profiles that prompt for a username/password must have `auth-user-pass` inlined; encrypted PKCS#12 can't be used. Surface the `message`. In BYO mode the user re-imports a fixed profile; in ephemeral mode fix `ovpn_provision_cmd`'s output.
- **`vpn_status()` shows the session but the command still can't reach the host.** Tunnel up without routing/DNS — confirm with `vpn_status()` and report both the session state and the original command's error; don't just re-run `vpn_connect`.
- **Multiple simultaneous tasks share a profile.** The tunnel is a shared resource. Connect at the start of the VPN-requiring block of work and disconnect only when no further VPN-gated step is queued.

## Additional resources

- `references/example-local-settings.md` — full commented templates for both modes.
````

- [ ] **Step 2: Overwrite `references/example-local-settings.md`**

Write `PLUGIN/skills/vpn-on-demand/references/example-local-settings.md` with exactly:

````markdown
# Example `.claude/openvpn3-on-demand.local.md`

Drop this at `.claude/openvpn3-on-demand.local.md` in the project root, fill in
**one** of the two modes below, and add `.claude/*.local.md` to `.gitignore`.

## BYO mode — use an openvpn3 config you've already imported

```markdown
---
# REQUIRED in this mode. Name of an openvpn3 config you imported yourself, e.g.
#   openvpn3 config-import --config /path/to/my-prod-vpn.ovpn \
#                          --name my-prod-vpn --persistent
# The plugin only starts/stops sessions for it — it never creates or removes
# this config.
profile_name: my-prod-vpn

# OPTIONAL (both modes). Extra regex patterns to treat as VPN-requiring, on top
# of the vpn-on-demand skill's built-in defaults. Each entry is matched against
# the full command string or the target URL/host.
trigger_patterns:
  - "mysql .* -h [^ ]*\\.internal"
  - "kubectl --context prod-.*"

# OPTIONAL (both modes). Shell command run after a fresh vpn_connect
# (status: connected, not already_connected). Typical uses: warming a DNS cache,
# probing a VPC endpoint, opening an ssh control master, audit logging. A
# non-zero exit is surfaced but does NOT tear down the tunnel.
post_connect_cmd: dig +short internal-db.my-vpc.internal

# OPTIONAL (both modes). Shell command run after a fresh vpn_disconnect
# (status: disconnected, not not_connected). Typical uses: flushing a DNS
# resolver cache, closing port-forwards opened by post_connect_cmd. Also run by
# the Stop/SessionEnd safety-net hook when it disconnects (5s timeout, failures
# swallowed) — keep it quick and idempotent.
post_disconnect_cmd: sudo resolvectl flush-caches
---

# Project-specific VPN notes for humans

Which account this profile is for, how to rotate its credentials, who to ping
when it breaks. This body is not consumed by the plugin.
```

## Ephemeral mode — generate a throwaway profile, regenerated every turn

```markdown
---
# REQUIRED in this mode (mutually exclusive with profile_name). A shell command
# whose STANDARD OUTPUT is the contents of a .ovpn file. The plugin captures
# stdout into a private temp file, imports it as a single-use config under an
# internal name you never see or pick, uses it, and lets openvpn3 drop it once
# the tunnel starts. Re-run on every turn that needs the VPN.
#
# Examples (pick whatever fits how you store the profile):
#   ovpn_provision_cmd: vault read -field=config secret/vpn/my-prod
#   ovpn_provision_cmd: aws s3 cp s3://my-bucket/vpn/my-prod.ovpn -
#   ovpn_provision_cmd: cat ~/.config/openvpn3/my-prod-vpn.ovpn
#   ovpn_provision_cmd: make get_vpn_client_config OUTPUT=/dev/stdout
ovpn_provision_cmd: vault read -field=config secret/vpn/my-prod

# OPTIONAL fields below behave exactly as in BYO mode — see above for the full
# descriptions.
trigger_patterns:
  - "aws (rds|elasticache|memorydb|secretsmanager|ssm) "
post_connect_cmd: dig +short internal-db.my-vpc.internal
post_disconnect_cmd: sudo resolvectl flush-caches
---

# Project-specific VPN notes for humans

Where ovpn_provision_cmd pulls the profile from, what credentials it needs, who
owns that secret. This body is not consumed by the plugin.
```

## Pick exactly one mode

- `profile_name` **xor** `ovpn_provision_cmd`. Setting both, or neither, is a configuration error — the skill tells the user and skips the VPN entirely.
- `profile_name` is for a config you manage yourself with `openvpn3 config-import --persistent`; the plugin never creates or deletes it.
- `ovpn_provision_cmd` is for a throwaway profile regenerated on every VPN-gated turn; its **stdout must be the `.ovpn` body** — not a file path, not a status line.

## Gitignore

The settings file holds environment-specific names and commands that may point at
internal infrastructure. Treat it as local state:

```gitignore
.claude/*.local.md
```
````

- [ ] **Step 3: Sanity-check the skill frontmatter**

```bash
head -4 plugins/openvpn3-on-demand/skills/vpn-on-demand/SKILL.md
```
Expected: the YAML frontmatter block (`---`, `name: vpn-on-demand`, `description: ...`, `---`) is intact and unchanged from the original.

- [ ] **Step 4: Commit**

```bash
git add plugins/openvpn3-on-demand/skills/vpn-on-demand/SKILL.md \
        plugins/openvpn3-on-demand/skills/vpn-on-demand/references/example-local-settings.md
git commit -m "$(cat <<'EOF'
Rewrite vpn-on-demand skill for BYO vs ephemeral modes

SKILL.md preflight now validates exactly-one-of {profile_name,
ovpn_provision_cmd}, and the core flow is split into a BYO flow (use an
existing imported config) and an ephemeral flow (provision from
ovpn_provision_cmd's stdout into a single-use ovpn3-od-<session id>
config each turn). example-local-settings.md gets templates for both
modes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: update `README.md` and the plugin `CLAUDE.md`

Surgical edits — these files are mostly unchanged. Make each change exactly as written.

**Files:**
- Modify: `PLUGIN/README.md`
- Modify: `PLUGIN/CLAUDE.md` (currently untracked — `git add` it in this task's commit)

- [ ] **Step 1: `README.md` — Components table row for the settings file**

Find this row in the `## Components` table:

```
| `.claude/openvpn3-on-demand.local.md`     | Per-project settings (user-owned, git-ignored). Declares the profile name, optional provision command, optional extra trigger patterns. |
```

Replace it with:

```
| `.claude/openvpn3-on-demand.local.md`     | Per-project settings (user-owned, git-ignored). Picks one mode: `profile_name` (use an openvpn3 config you imported yourself) or `ovpn_provision_cmd` (generate a single-use throwaway config from the command's stdout each turn). Plus optional `trigger_patterns` / `post_connect_cmd` / `post_disconnect_cmd`. |
```

- [ ] **Step 2: `README.md` — replace the "Per-project setup" section**

Replace everything from the line `## Per-project setup` up to (but not including) the next heading `## How it behaves` with:

````markdown
## Per-project setup

1. Create `.claude/openvpn3-on-demand.local.md` in your project root and pick **one** mode.

   **BYO mode** — you maintain the openvpn3 config yourself:

   ```markdown
   ---
   profile_name: my-prod-vpn
   # optional: trigger_patterns, post_connect_cmd, post_disconnect_cmd
   ---
   ```

   then import the profile once:

   ```bash
   openvpn3 config-import --config /path/to/my-prod-vpn.ovpn \
                          --name my-prod-vpn --persistent
   ```

   **Ephemeral mode** — a command produces the `.ovpn` body on stdout; the plugin makes a single-use config from it each time the tunnel is needed:

   ```markdown
   ---
   ovpn_provision_cmd: vault read -field=config secret/vpn/my-prod
   # optional: trigger_patterns, post_connect_cmd, post_disconnect_cmd
   ---
   ```

   `ovpn_provision_cmd` must write the `.ovpn` contents to **standard output** (e.g. `aws s3 cp s3://…/vpn.ovpn -`, `cat ~/.config/openvpn3/my.ovpn`, `make get_vpn_client_config OUTPUT=/dev/stdout`). The plugin captures stdout into a private temp file (mode 600), imports it `single_use`, and deletes it — the contents never enter the conversation transcript. Nothing to import by hand in this mode.

   `profile_name` and `ovpn_provision_cmd` are mutually exclusive; setting both, or neither, is a configuration error and the skill skips the VPN. See `skills/vpn-on-demand/references/example-local-settings.md` for the full commented templates.

2. Add the settings file to your project's `.gitignore`:

   ```gitignore
   .claude/*.local.md
   ```
````

- [ ] **Step 3: `README.md` — replace the "How it behaves" section**

Replace everything from `## How it behaves` up to (but not including) the next heading `## MCP tools` with:

````markdown
## How it behaves

- The skill loads when Claude sees a request that plausibly touches a private resource (RDS/ElastiCache/MemoryDB hosts, RFC1918 addresses targeted by a remote-access verb, `.internal` / `.corp` / `.private` / `.vpc` hostnames, `aws` CLI against private services, private `kubectl` contexts, etc., plus anything in the project's `trigger_patterns`).
- Its preflight reads `.claude/openvpn3-on-demand.local.md` to learn the mode (`profile_name` → BYO, `ovpn_provision_cmd` → ephemeral) and does the one-time host DNS check.

  **BYO mode:**
  - Claude calls `vpn_connect(profile_name)`. If the named config isn't imported, Claude stops and tells you to `openvpn3 config-import` it — no auto-provisioning.
  - On a fresh connect, `post_connect_cmd` (if set) runs.
  - Claude runs your command; subsequent VPN-gated commands reuse the tunnel.
  - At task end, Claude calls `vpn_disconnect(profile_name)`; on a fresh disconnect, `post_disconnect_cmd` (if set) runs.

  **Ephemeral mode:** the config name is internal — `ovpn3-od-<Claude session id>`.
  - For each VPN-gated turn, Claude tries `vpn_connect` on that name; if there's nothing yet, it runs `ovpn_provision_cmd` (stdout → a temp `.ovpn`), `vpn_config_import(..., single_use=True)`, deletes the temp file, then `vpn_connect`. openvpn3 drops the config once the tunnel starts.
  - `post_connect_cmd` runs after a fresh connect; the user's command runs; `vpn_disconnect` + `post_disconnect_cmd` at task end.
  - `ovpn_provision_cmd` re-runs on every turn that touches the VPN — the profile is per-turn-disposable by design.

- The Stop and SessionEnd hooks run `teardown.py` as a safety net for when Claude forgot the disconnect step. In BYO mode it disconnects the named profile's session (and leaves the config alone); in ephemeral mode it disconnects `ovpn3-od-<session id>` **and** removes that config. Either way, if `post_disconnect_cmd` is set it runs it (5 s timeout, silent failure). The hook touches only the one profile this project is responsible for — never a blanket disconnect.
````

- [ ] **Step 4: `README.md` — `vpn_config_import` row in the MCP tools table**

Find this row in the `## MCP tools` table:

```
| `vpn_config_import`   | `ovpn_path`, `profile_name`    | `{status: imported / already_imported / error, ...}`              |
```

Replace it with:

```
| `vpn_config_import`   | `ovpn_path`, `profile_name`, `single_use` (default `false`) | `{status: imported / already_imported / error, ...}` |
```

And immediately after the table, find the paragraph that begins:

```
`profile_name` is required on `vpn_disconnect` and `vpn_config_remove`;
```

and append this sentence to the end of that paragraph (before the blank line):

```
 `single_use=true` imports an ephemeral, memory-only config that openvpn3 drops once a tunnel is started from it — the skill uses it for `ovpn_provision_cmd` profiles.
```

- [ ] **Step 5: `README.md` — Security notes: the `.ovpn` reading bullet**

Find this bullet under `## Security notes`:

```
- The plugin reads the `.ovpn` file only to pass it to `openvpn3.ConfigParser`,
  which inlines any external cert/key references and hands the result to the
  openvpn3 configuration manager over D-Bus. The secret material lives inside
  openvpn3 after import; the plugin doesn't cache it.
```

Replace it with:

```
- The plugin reads the `.ovpn` file only to hand its contents to the openvpn3
  configuration manager over D-Bus (which does the authoritative parsing). In
  ephemeral mode the file is a `mktemp` file (mode 600) holding `ovpn_provision_cmd`'s
  stdout; it exists only between provisioning and import, then is deleted, and its
  bytes are never echoed into the conversation transcript. The secret material lives
  inside openvpn3 after import; the plugin doesn't cache it.
```

- [ ] **Step 6: `README.md` — Troubleshooting: the unknown-config bullet**

Find this bullet under `## Troubleshooting`:

```
- **`vpn_connect` errors about an unknown config** — the profile isn't
  imported yet. Either set `ovpn_provision_cmd` in the settings file or run
  `openvpn3 config-import` manually.
```

Replace it with:

```
- **`vpn_connect` errors about an unknown config** — in BYO mode the profile
  isn't imported; run `openvpn3 config-import --name <profile_name> --persistent`.
  In ephemeral mode this normally just means the skill needs to (re)provision —
  check that `ovpn_provision_cmd` is set and that it writes a valid `.ovpn` to
  stdout. If ephemeral connects fail before any tool runs, check that
  `CLAUDE_CODE_SESSION_ID` is set in the environment (very old Claude Code
  versions don't set it; the skill declines ephemeral mode without it).
```

- [ ] **Step 7: `README.md` — add a migration note**

After the `## Licensing` section (and before `## Troubleshooting`), insert a new section:

````markdown
## Migrating to 0.5.0

`profile_name` is now optional, but the settings file must declare **exactly one** of `profile_name` / `ovpn_provision_cmd`:

- Had only `profile_name` → no change.
- Had **both** `profile_name` and `ovpn_provision_cmd` → that's now a configuration error. Pick one: drop `ovpn_provision_cmd` and `openvpn3 config-import` the profile yourself once, **or** drop `profile_name` to use ephemeral mode.
- `ovpn_provision_cmd` semantics changed: it must now write the `.ovpn` **contents to stdout**, not write a file to a path it picks / print that path. A command like `make get_vpn_client_config OUTPUT=~/.config/openvpn3/my.ovpn` becomes `make get_vpn_client_config OUTPUT=/dev/stdout`; `aws s3 cp s3://…/vpn.ovpn ~/vpn.ovpn` becomes `aws s3 cp s3://…/vpn.ovpn -`.

````

- [ ] **Step 8: `PLUGIN/CLAUDE.md` — update the architecture summary and the version-bump gotcha**

In `plugins/openvpn3-on-demand/CLAUDE.md`:

(a) Find the `- **Skill** — ...` bullet:

```
- **Skill** — `skills/vpn-on-demand/SKILL.md`. The **policy layer**: decides *when* Claude calls the MCP tools (RDS/ElastiCache/MemoryDB hosts, RFC1918 + a remote-access verb, `.internal`/`.corp` hostnames, private kubectl, project-defined `trigger_patterns`, …) and drives the connect → provision → import → disconnect flow. Carries no code. Full settings template at `skills/vpn-on-demand/references/example-local-settings.md`.
```

Replace it with:

```
- **Skill** — `skills/vpn-on-demand/SKILL.md`. The **policy layer**: decides *when* Claude calls the MCP tools (RDS/ElastiCache/MemoryDB hosts, RFC1918 + a remote-access verb, `.internal`/`.corp` hostnames, private kubectl, project-defined `trigger_patterns`, …) and which of the two modes the project uses — **BYO** (`profile_name` = an existing imported config) or **ephemeral** (`ovpn_provision_cmd` = a command whose stdout is the `.ovpn` body → a single-use config named `ovpn3-od-$CLAUDE_CODE_SESSION_ID`, re-provisioned every VPN-gated turn). Exactly one of the two fields; both/neither is a config error. Carries no code. Full settings templates at `skills/vpn-on-demand/references/example-local-settings.md`.
```

(b) Find the per-project config bullet:

```
- **Per-project config** — `.claude/openvpn3-on-demand.local.md` (git-ignored, YAML frontmatter: `profile_name` required; `ovpn_provision_cmd`, `trigger_patterns`, `post_connect_cmd`, `post_disconnect_cmd` optional). The skill re-reads it every turn and `teardown.py` re-reads it on every hook fire, so edits take effect immediately — only changes to `.mcp.json` / `hooks.json` need a Claude Code restart. Full template: `skills/vpn-on-demand/references/example-local-settings.md`.
```

Replace it with:

```
- **Per-project config** — `.claude/openvpn3-on-demand.local.md` (git-ignored, YAML frontmatter: exactly one of `profile_name` / `ovpn_provision_cmd`; `trigger_patterns`, `post_connect_cmd`, `post_disconnect_cmd` optional). The skill re-reads it every turn and `teardown.py` re-reads it on every hook fire, so edits take effect immediately — only changes to `.mcp.json` / `hooks.json` need a Claude Code restart. Full templates: `skills/vpn-on-demand/references/example-local-settings.md`.
```

(c) Find the version-bump gotcha bullet:

```
- **Version bumps span several files.** A plugin version lives in `plugins/<name>/.claude-plugin/plugin.json` *and* its entry in `.claude-plugin/marketplace.json`. For the openvpn3 plugin also bump `servers/openvpn3/pyproject.toml`, the stderr banner string in `server.py`'s `main()`, and any version mentions in `servers/openvpn3/README.md` / `launch.sh` comments.
```

Replace it with:

```
- **Version bumps span several files.** A plugin version lives in `plugins/<name>/.claude-plugin/plugin.json` *and* its entry in `.claude-plugin/marketplace.json`. For the openvpn3 plugin also bump `servers/openvpn3/pyproject.toml` and the stderr banner string in `server.py`'s `main()`. (The `0.4.0–0.4.2` / `≥0.4.3` strings in the plugin README's Troubleshooting and in `launch.sh` comments are historical references — leave them.)
```

- [ ] **Step 9: Commit**

```bash
git add plugins/openvpn3-on-demand/README.md plugins/openvpn3-on-demand/CLAUDE.md
git commit -m "$(cat <<'EOF'
Update openvpn3-on-demand README and CLAUDE.md for the two modes

Document BYO vs ephemeral mode, the new ovpn_provision_cmd
stdout-is-contents contract, the single_use vpn_config_import flag, the
mode-aware teardown hook, and a 0.5.0 migration note.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: bump the plugin to 0.5.0

**Files:**
- Modify: `PLUGIN/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json` (repo root)
- Modify: `PLUGIN/servers/openvpn3/pyproject.toml`
- Modify: `PLUGIN/servers/openvpn3/openvpn3_mcp/server.py` (the stderr banner in `main()`)

- [ ] **Step 1: Find every "current version" string**

```bash
grep -rn "0\.4\.3" plugins/openvpn3-on-demand .claude-plugin
```
Expected hits (4): `plugins/openvpn3-on-demand/.claude-plugin/plugin.json` (`"version": "0.4.3"`), `.claude-plugin/marketplace.json` (`"version": "0.4.3"` in the `openvpn3-on-demand` entry), `plugins/openvpn3-on-demand/servers/openvpn3/pyproject.toml` (`version = "0.4.3"`), `plugins/openvpn3-on-demand/servers/openvpn3/openvpn3_mcp/server.py` (`"openvpn3-mcp 0.4.3 — AGPL-3.0-only"`). If `grep` also turns up the historical `0.4.0–0.4.2` / `≥0.4.3` strings in `README.md` / `launch.sh`, **do not** change those.

- [ ] **Step 2: Update each of the 4**

- `plugins/openvpn3-on-demand/.claude-plugin/plugin.json`: `"version": "0.4.3"` → `"version": "0.5.0"`
- `.claude-plugin/marketplace.json`: in the `openvpn3-on-demand` entry, `"version": "0.4.3"` → `"version": "0.5.0"`
- `plugins/openvpn3-on-demand/servers/openvpn3/pyproject.toml`: `version = "0.4.3"` → `version = "0.5.0"`
- `plugins/openvpn3-on-demand/servers/openvpn3/openvpn3_mcp/server.py`: in `main()`, `print("openvpn3-mcp 0.4.3 — AGPL-3.0-only", file=sys.stderr)` → `print("openvpn3-mcp 0.5.0 — AGPL-3.0-only", file=sys.stderr)`

- [ ] **Step 3: Verify**

```bash
grep -rn "0\.5\.0" plugins/openvpn3-on-demand .claude-plugin
grep -rn '"0\.4\.3"\|version = "0\.4\.3"\|mcp 0\.4\.3' plugins/openvpn3-on-demand .claude-plugin
```
Expected: the first grep shows 4 hits; the second shows nothing.

- [ ] **Step 4: Commit**

```bash
git add plugins/openvpn3-on-demand/.claude-plugin/plugin.json \
        .claude-plugin/marketplace.json \
        plugins/openvpn3-on-demand/servers/openvpn3/pyproject.toml \
        plugins/openvpn3-on-demand/servers/openvpn3/openvpn3_mcp/server.py
git commit -m "$(cat <<'EOF'
openvpn3-on-demand 0.5.0 — BYO vs ephemeral profiles

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: full verification

No commit — just confirm everything is green.

- [ ] **Step 1: Run the whole test suite**

```bash
cd plugins/openvpn3-on-demand/servers/openvpn3
uv sync --group dev --quiet
uv run --quiet pytest -q
```
Expected: all tests pass (the original `test_server.py` set + the new `test_vpn_config_import_single_use` + every test in `test_teardown.py`).

- [ ] **Step 2: Validate the manifests**

```bash
cd /home/aeresov/workshop/aeresov/claude-plugins
claude plugin validate .
claude plugin validate plugins/openvpn3-on-demand
```
Expected: both print `✔ Validation passed`.

- [ ] **Step 3: Eyeball the diff**

```bash
git log --oneline openvpn3-ephemeral-profiles ^main
git diff main...openvpn3-ephemeral-profiles --stat
```
Expected commits (newest first): `openvpn3-on-demand 0.5.0 …`, `Update openvpn3-on-demand README and CLAUDE.md …`, `Rewrite vpn-on-demand skill …`, `teardown hook: mode-aware teardown …`, `teardown hook: add _resolve_target …`, `Add single_use flag …`, `Spec: openvpn3-on-demand BYO vs. ephemeral …`. The `--stat` should show only files under `plugins/openvpn3-on-demand/`, `.claude-plugin/marketplace.json`, and `docs/superpowers/` — and **not** `./CLAUDE.md`.

- [ ] **Step 4: Report**

State explicitly: test count + "all passed", both `claude plugin validate` results, and confirm `./CLAUDE.md` was left untouched. If anything failed, stop and report — do not paper over it.
