# CLAUDE.md

Developer notes for `openvpn3-on-demand`. User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## Layout

- `servers/openvpn3/` — MCP server (uv project; package `openvpn3_mcp`, script `openvpn3-mcp`, Python ≥ 3.10). Wraps openvpn3's D-Bus services via the `openvpn3` Python module + `dbus-python`. Four tools: `vpn_status`, `vpn_connect`, `vpn_connect_ephemeral`, `vpn_disconnect`. Launched by `.mcp.json` via `scripts/launch.sh`.
- `servers/openvpn3/src/openvpn3_mcp/dnscheck.py` — host DNS check (checklist check 9, the skill's post-connect check). Not imported by `server.py`; doctor, setup and the skill run it **by path** with the system `python3`, so it stays stdlib-only, Python 3.10 syntax, no relative imports (`-m` would import `__init__` → `server` → `mcp`). `tests/test_dnscheck.py` guards both. Exit 0 OK / 1 FAIL / 3 WARN / 2 bad usage. `--connected` (skill, after connecting) judges a host no tunnel domain covers by what programs get; `--url-stdin` reads the host from a credential URL on stdin without ever printing it; the script never echoes its arguments.
- `skills/vpn-on-demand/SKILL.md` — policy layer: decides *when* Claude calls the tools and which mode the project uses. Owns connect *and* disconnect; no safety-net hook, so a tunnel orphaned by a crash needs `openvpn3 session-manage --disconnect --config <name>` by hand.
- `commands/setup.md` + `commands/doctor.md` — `/openvpn3-on-demand:setup` (interactive configurator) and `/openvpn3-on-demand:doctor` (read-only health check). Both read `setup-checklist.md` (9 checks); doctor restates the list in step 2, so renumbering means editing both (a49e226 missed that). Prose plus `dnscheck.py`; neither runs `sudo` or `vpn_*` tools.
- `.claude/openvpn3-on-demand.local.md` (consuming project) — per-project settings. Frontmatter: exactly one of `profile_name` (BYO) / `ovpn_provision_cmd` (ephemeral); optional `post_connect_cmd`, `post_disconnect_cmd`, `config_overrides`. Re-read every turn, so edits apply immediately — except that a `config_overrides` key removed in BYO mode stays in the profile (the stateless server only calls `SetOverride`, never `UnsetOverride`).

## Commands

```bash
cd servers/openvpn3
make check                                               # ruff + format check + ty + pytest; D-Bus / openvpn3 are stubbed in `tests/conftest.py`
python3 src/openvpn3_mcp/dnscheck.py [HOSTNAME]          # host DNS check against this machine (read-only)
make tidyup                                              # ruff autofix + format
uv run openvpn3-mcp                                      # stdio server (expects an MCP client)
claude --plugin-dir /absolute/path/to/openvpn3-on-demand # load into a live session
```

## Gotchas

- **Don't reduce `scripts/launch.sh` to a plain `uv run`.** `dbus` / `openvpn3` come from system site-packages, invisible to uv's managed interpreter; the venv needs `uv venv --python /usr/bin/python3 --system-site-packages`. The launcher does that on first run but doesn't heal stale venvs — `rm -rf .venv` if one predates this layout.
- **Never pre-parse `.ovpn` files with `openvpn3.ConfigParser`.** Its directive whitelist rejects valid configs (AWS Client VPN's `remote-random-hostname`). Hand raw bytes to `ConfigurationManager.Import` over D-Bus; openvpn3's parser is authoritative. Shipped broken in 0.4.0.
- **DNS has two layers; `netcfg.json` only proves the first.** openvpn3-service-netcfg pushes the tunnel's DNS into systemd-resolved; programs must still *ask* it (nss-resolve, or `/etc/resolv.conf` = the 127.0.0.53 stub). If Tailscale, a VPN client or NetworkManager owns `/etc/resolv.conf`, `resolvectl query` works while `getent` gets `0.0.0.0` (rebinding-protected resolvers) and clients hang on local services; Tailscale can stay stuck owning it after an unclean stop (tailscale/tailscale#19062). `dnscheck.py` detects this; the plugin prints fixes, never runs them.
- **AGPL-3.0-only.** The server links the AGPL `openvpn3` Python module. New Python/shell files start with `# SPDX-License-Identifier: AGPL-3.0-only`. `server.py`'s `main()` prints the AGPL §5(d) notice to **stderr** — stdout is reserved for the MCP stdio protocol.
- **Version bumps touch four files:** `.claude-plugin/plugin.json`, the repo-root `.claude-plugin/marketplace.json` entry, `servers/openvpn3/pyproject.toml`, and the `openvpn3-mcp` entry in `servers/openvpn3/uv.lock` (`uv sync` regenerates it). The stderr banner follows pyproject via `importlib.metadata`. CI enforces all four: the repo-root `scripts/check-version-sync.sh` checks the first three, `uv sync --locked` the lock.
