# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working inside the `openvpn3-on-demand` plugin. See the repository-root `CLAUDE.md` for marketplace-wide conventions.

## What this plugin is

Linux-only. Raises an OpenVPN3 tunnel on demand — right before a command that needs private-network access — and tears it down at session end as a safety net. It coordinates three layers around `openvpn3-linux`:

- **MCP server** — `servers/openvpn3/` (a `uv` project; Python package `openvpn3_mcp`, console script `openvpn3-mcp`, requires Python ≥ 3.10). Wraps the openvpn3 D-Bus services (`net.openvpn.v3.configuration`, `net.openvpn.v3.sessions`) via the `openvpn3` Python module + `dbus-python` — **no CLI shell-out, no stdout parsing**. Tools: `vpn_status`, `vpn_connect`, `vpn_disconnect`, `vpn_config_import`, `vpn_config_remove`; each returns a `{"status": ...}` dict and degrades to `{"status": "error", ...}` (never raises) when the system deps are missing. Wired up by `.mcp.json`, which runs it through `scripts/launch.sh`.
- **Skill** — `skills/vpn-on-demand/SKILL.md`. The **policy layer**: decides *when* Claude calls the MCP tools (RDS/ElastiCache/MemoryDB hosts, RFC1918 + a remote-access verb, `.internal`/`.corp` hostnames, private kubectl, project-defined `trigger_patterns`, …) and which of the two modes the project uses — **BYO** (`profile_name` = an existing imported config) or **ephemeral** (`ovpn_provision_cmd` = a command whose stdout is the `.ovpn` body → a single-use config named `ovpn3-od-$CLAUDE_CODE_SESSION_ID`, re-provisioned every VPN-gated turn). Exactly one of the two fields; both/neither is a config error. Carries no code. Full settings templates at `skills/vpn-on-demand/references/example-local-settings.md`.
- **Hooks** — `hooks/hooks.json` + `hooks/scripts/teardown.py`. Stop + SessionEnd safety net: disconnects the configured profile via the same D-Bus API if the model forgot to. Silent no-op when deps are missing; **scoped to the single profile named in the project settings file — never blanket-disconnects.**

Per-project config: `.claude/openvpn3-on-demand.local.md` in the consuming project (git-ignored; YAML frontmatter — exactly one of `profile_name` / `ovpn_provision_cmd`; `trigger_patterns` / `post_connect_cmd` / `post_disconnect_cmd` optional). The skill re-reads it every turn and `teardown.py` re-reads it on every hook fire, so edits apply immediately — only `.mcp.json` / `hooks.json` changes need a Claude Code restart. `${CLAUDE_PLUGIN_ROOT}` resolves plugin-relative paths in `.mcp.json` / `hooks.json`; `CLAUDE_PROJECT_DIR` (set in the hook env) is how `teardown.py` finds the settings file.

## Commands

```bash
# MCP server test suite (uv required) — run from servers/openvpn3/
cd servers/openvpn3
uv sync --group dev
uv run pytest -q
uv run pytest -q tests/test_server.py::test_vpn_connect_happy_path   # single test

# Run the server standalone (stdio; expects an MCP client on the other end)
uv run openvpn3-mcp

# Load the plugin into a live Claude Code session for manual testing
claude --plugin-dir <absolute path to this directory>
```

Tests stub the `dbus` / `openvpn3` modules in `tests/conftest.py`, so the suite runs anywhere — CI does not install `openvpn3-client` or `python3-dbus`.

## Gotchas

- **`scripts/launch.sh` is load-bearing — don't reduce it to a plain `uv run`.** The server imports `dbus` / `openvpn3` from the **system** site-packages (`/usr/lib/python3/dist-packages/...`), so the venv must be created with `--system-site-packages` *and* pinned to `/usr/bin/python3` — `uv venv`'s default managed interpreter can't see those packages (this was the 0.4.0–0.4.2 bug). The launcher detects a venv that doesn't satisfy both and recreates it.
- **Never pre-parse `.ovpn` files with `openvpn3.ConfigParser`.** It's argparse-backed with a directive whitelist and rejects valid real-world configs (e.g. AWS Client VPN's `remote-random-hostname`) — that regression shipped in 0.4.0. Hand the raw file contents to `ConfigurationManager.Import` over D-Bus; openvpn3's own parser is authoritative. Guarded by `test_vpn_config_import_passes_raw_ovpn_contents`.
- **A version bump touches several files:** `.claude-plugin/plugin.json`, this plugin's entry in the repo-root `.claude-plugin/marketplace.json`, `servers/openvpn3/pyproject.toml`, and the stderr banner string in `server.py`'s `main()`. (The `0.4.0–0.4.2` / `≥0.4.3` strings in the README's Troubleshooting and in `launch.sh` comments are historical references — leave them.)
- **AGPL-3.0-only**, because the MCP server and `teardown.py` link the AGPL `openvpn3` Python module. New Python/shell files start with `# SPDX-License-Identifier: AGPL-3.0-only`. `NOTICE` must travel with redistributions; `server.py`'s `main()` prints the AGPL notice to **stderr** (stdout is reserved for the MCP stdio protocol).
- **`python3-dbus` is not auto-installed by `openvpn3-client`** — minimal containers need it explicitly. Both missing deps surface as `{"status": "error", ...}`, never a crash.
