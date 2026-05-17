# openvpn3-on-demand

A Claude Code plugin that raises an OpenVPN3 tunnel right before a command that needs private-network access and tears it down at task end. No always-on VPN, no per-command `Bash(openvpn3 *)` permission grants.

**Linux only.** Talks to `openvpn3-linux` over its D-Bus services via the `openvpn3` Python module shipped with `openvpn3-client`.

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install openvpn3-on-demand@aeresov-claude-plugins
/openvpn3-on-demand:setup     # writes .claude/openvpn3-on-demand.local.md + .gitignore
```

Then just ask Claude to do something that hits a private resource. The skill picks it up and connects before running the command.

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

- **BYO mode** — you `openvpn3 config-import --persistent` a profile yourself; set `profile_name` to its name. The plugin only starts/stops sessions for it.
- **Ephemeral mode** — set `ovpn_provision_cmd` to a shell command whose **stdout is the `.ovpn` body**. The plugin captures stdout into a mode-600 temp file, imports it as a single-use config under `ovpn3-od-$CLAUDE_CODE_SESSION_ID`, connects, deletes the temp file. Re-runs every VPN-gated turn.

Setting both fields or neither is a configuration error and the skill skips the VPN.

See [`skills/vpn-on-demand/references/example-local-settings.md`](skills/vpn-on-demand/references/example-local-settings.md) for full commented templates of both modes.

## MCP tools

| Tool                    | Args                          | Returns                                                  |
|-------------------------|-------------------------------|----------------------------------------------------------|
| `vpn_status`            | —                             | `{status: ok, session_count, sessions: [...]}`           |
| `vpn_connect`           | `profile_name`, `overrides?`  | `{status: connected \| already_connected \| error, ...}` |
| `vpn_connect_ephemeral` | `ovpn_path`, `overrides?`     | `{status: connected \| already_connected \| error, ...}` |
| `vpn_disconnect`        | `profile_name`                | `{status: disconnected \| not_connected \| error, ...}`  |

`vpn_connect` is for BYO profiles (already imported). `vpn_connect_ephemeral` reads a freshly-written `.ovpn` file and imports it as single-use. Both apply `dns-scope=tunnel` as a baseline (split-DNS, so the VPN coexists with Tailscale's MagicDNS / mDNS); pass `overrides` to override.

## Security

- The `.ovpn` body is written to a mode-600 `mktemp` file, handed to openvpn3 over D-Bus, and deleted. Its bytes never enter the conversation transcript.
- `.claude/openvpn3-on-demand.local.md` may contain internal hostnames and provisioning commands — `/openvpn3-on-demand:setup` adds it to `.gitignore`.
- The MCP server exits 1 if `dbus` or `openvpn3` aren't importable. It never installs anything and never runs as root.
- The skill targets only the profile named in the settings file — never a blanket disconnect. If Claude Code crashes mid-task the tunnel stays up; clean it up with `openvpn3 session-manage --disconnect --config <profile_name>` (BYO) or `openvpn3 session-manage --disconnect --config "ovpn3-od-$CLAUDE_CODE_SESSION_ID"` (ephemeral).

## Troubleshooting

- **Anything looks broken** — run `/openvpn3-on-demand:doctor`. It tests every prerequisite and prints the exact fix for each failure.
- **MCP server exits with `cannot import 'dbus' and/or 'openvpn3'`** — install `openvpn3-client` and `python3-dbus`, then restart Claude Code.
- **`vpn_connect` says the config is unknown** — in BYO mode, import it: `openvpn3 config-import --name <profile_name> --persistent --config /path/to/file.ovpn`.
- **Tunnel up but `*.rds.amazonaws.com` fails to resolve** — the one-time host DNS init in [Prerequisites](#prerequisites) wasn't run.

Edits to `.claude/openvpn3-on-demand.local.md` take effect immediately — no restart. Only changes to the plugin's own `.mcp.json` require restarting Claude Code.

## License

AGPL-3.0-only. The plugin links the AGPL `openvpn3` Python module; the whole `claude-plugins` repo is AGPL for consistency. See [LICENSE](../../LICENSE).
