# openvpn3-on-demand

A Claude Code plugin that brings an OpenVPN3 tunnel up/down on demand — no always-on VPN, no `Bash(openvpn3 *)` permission grants.
**Linux only.** Talks to `openvpn3-linux` over its D-Bus services via the `openvpn3` Python module shipped with `openvpn3-client`.

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install openvpn3-on-demand@aeresov-claude-plugins
/openvpn3-on-demand:setup     # writes .claude/openvpn3-on-demand.local.md + .gitignore
```

Then just ask Claude to do something that hits a private resource. The skill picks it up and connects before running the command — see [When does the tunnel come up?](#when-does-the-tunnel-come-up) for how Claude decides, and how to tell it about resources it can't recognise on its own.

Run `/openvpn3-on-demand:doctor` at any time for a read-only health check.

## Prerequisites

- `openvpn3-client` — provides the D-Bus services and the `openvpn3` Python module. Debian/Ubuntu: `apt install openvpn3-client`.
- `python3-dbus` — not pulled in automatically by `openvpn3-client`. Debian/Ubuntu: `apt install python3-dbus`.
- `python3` ≥ 3.10 on `PATH`.
- `uv` — runs the MCP server. <https://docs.astral.sh/uv/>.
- One-time host DNS init (so `*.rds.amazonaws.com` etc. resolve over the tunnel on systemd hosts):
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```

`/openvpn3-on-demand:doctor` checks all of the above.

## Modes

Pick one in `.claude/openvpn3-on-demand.local.md`:

- **BYO mode** — you `openvpn3 config-import --persistent` a profile yourself; set `profile_name` to its name. The plugin starts/stops sessions for it, and before each start it writes the `dns-scope=tunnel` baseline plus any `config_overrides` into that profile as openvpn3 overrides. Those are stored in the profile (on disk, for a `--persistent` one), so they also apply to your own `openvpn3 session-start`. Removing a key from `config_overrides` does **not** remove it from the profile. To remove one, run `openvpn3 config-manage --config <profile_name> --unset-override <key>`, and see what's set with `openvpn3 config-manage --config <profile_name> --show`.
- **Ephemeral mode** — set `ovpn_provision_cmd` to a shell command whose **stdout is the `.ovpn` body**. The plugin captures stdout into a mode-600 file in a private per-user directory, imports it as a single-use config under `ovpn3-od-$CLAUDE_CODE_SESSION_ID`, connects, and deletes the file. Re-runs every VPN-gated turn.

Setting both fields or neither is a configuration error and the skill skips the VPN.

See [`skills/vpn-on-demand/references/example-local-settings.md`](skills/vpn-on-demand/references/example-local-settings.md) for full commented templates of both modes.

## When does the tunnel come up?

**Claude decides, operation by operation.** The plugin gives it tools to connect and disconnect, and the skill tells it to connect before anything that needs your private network. But nothing in the plugin watches traffic, and it can't know on its own which resources sit behind your VPN. Claude works from:

- **Built-in hints** — private AWS endpoints (`*.rds.amazonaws.com`, `*.elasticache.amazonaws.com`, …), hostnames ending in `.internal`, `.corp`, `.private` or `.vpc`, and private IP addresses used with `ssh`, `kubectl`, `mysql`, `psql`, `curl` and similar.
- **Your project's `CLAUDE.md` / README** — anything they describe as internal or VPN-only.

What Claude can't see, it can't judge. Many operations never show their target in the command: a database client that reads its host from a config file or login path (the `mysql-client` plugin works this way), `kubectl` with a context from your kubeconfig, an SSH alias from `~/.ssh/config`, a `make` target or script, a secret-store CLI fetching credentials from an internal Vault, or any other tool or plugin that reads its endpoint from its own settings. For those, tell Claude:

- **In the project's `CLAUDE.md`** (best — Claude reads it every session, and plain words cover indirect cases):
  ```markdown
  The staging database, vault.corp.example and the Kubernetes API are only reachable over the VPN.
  ```
- **Or just ask** — "connect the VPN first".

If something that might be private fails with a timeout or "could not resolve host", the tunnel probably wasn't up. The skill tells Claude to suspect the VPN first, and you can always say so.

## MCP tools

| Tool                    | Args                          | Returns                                                  |
|-------------------------|-------------------------------|----------------------------------------------------------|
| `vpn_status`            | —                             | `{status: ok, session_count, sessions: [...]}`           |
| `vpn_connect`           | `profile_name`, `overrides?`  | `{status: connected \| already_connected \| error, ...}` |
| `vpn_connect_ephemeral` | `ovpn_path`, `session_id`, `overrides?` | `{status: connected \| already_connected \| error, ...}` |
| `vpn_disconnect`        | `profile_name`                | `{status: disconnected \| not_connected \| error, ...}`  |

`vpn_connect` is for already-imported BYO profiles. `vpn_connect_ephemeral` reads a freshly-written `.ovpn` and imports it single-use; the skill forwards `$CLAUDE_CODE_SESSION_ID` as `session_id` because the MCP server (a singleton across `/resume`/`/fork-session`) can't see that env var itself. Both apply `dns-scope=tunnel` as a baseline (split-DNS so the tunnel coexists with Tailscale / mDNS); pass `overrides` to override. Values the profile already holds aren't rewritten; in BYO mode the rest persist in your profile (see [Modes](#modes)).

`already_connected` means the existing session is actually up; one that's still connecting is waited for. A session under that name in any other state (paused, failed, backend dead) comes back as an `error` naming the state and the command that clears it (`openvpn3 session-manage --disconnect --config <name>`, or `openvpn3 session-manage --cleanup` for a dead backend). The server never tears down a session it didn't start in that call.

## Security

- In ephemeral mode the `.ovpn` body is written to a mode-600 file at a fixed per-session path in a private directory — `$XDG_RUNTIME_DIR/openvpn3-on-demand/` (memory-backed, wiped at logout) or `~/.cache/openvpn3-on-demand/` where that isn't set — handed to openvpn3 over D-Bus, and deleted straight after. Its bytes never enter the conversation transcript. An interrupted turn leaves at most that one file, which the next turn overwrites; `rm -rf` the directory to clear leftovers.
- `.claude/openvpn3-on-demand.local.md` may contain internal hostnames and provisioning commands — `/openvpn3-on-demand:setup` adds it to `.gitignore`.
- The MCP server exits 1 if `dbus` or `openvpn3` aren't importable. It never installs anything and never runs as root.
- The skill targets only the profile in the settings file — never a blanket disconnect. After a crash mid-task the tunnel stays up; clean it with `openvpn3 session-manage --disconnect --config <profile_name>` (BYO) or `… --config "ovpn3-od-$CLAUDE_CODE_SESSION_ID"` (ephemeral).

## Troubleshooting

- **Anything looks broken** — run `/openvpn3-on-demand:doctor`. It tests every prerequisite and prints the exact fix for each failure.
- **MCP server exits with `cannot import 'dbus' and/or 'openvpn3'`** — install `openvpn3-client` and `python3-dbus`, then restart Claude Code.
- **`vpn_connect` says the config is unknown** — in BYO mode, import it: `openvpn3 config-import --name <profile_name> --persistent --config /path/to/file.ovpn`.
- **Tunnel up but `*.rds.amazonaws.com` fails to resolve** — the one-time host DNS init in [Prerequisites](#prerequisites) wasn't run.

Edits to `.claude/openvpn3-on-demand.local.md` take effect immediately — no restart — with one exception in BYO mode: a key *removed* from `config_overrides` stays set in the profile until you run `openvpn3 config-manage --config <profile_name> --unset-override <key>`. Only changes to the plugin's own `.mcp.json` require restarting Claude Code.

## License

AGPL-3.0-only. The plugin links the AGPL `openvpn3` Python module; the whole `claude-plugins` repo is AGPL for consistency. See [LICENSE](../../LICENSE).
