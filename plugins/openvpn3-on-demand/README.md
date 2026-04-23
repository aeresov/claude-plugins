# openvpn3-on-demand

A Claude Code plugin that raises an OpenVPN3 tunnel on demand — right before
the model runs a command that needs private network access — and tears it
down at session end as a safety net. No always-on VPN, no per-command
`Bash(openvpn3 *)` permission grants.

## Components

| Piece                                     | What it does                                                                                                |
|-------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| `servers/openvpn3_mcp.py` (MCP server)    | Exposes `vpn_connect`, `vpn_disconnect`, `vpn_status`, `vpn_import` to Claude via `.mcp.json`.              |
| `skills/vpn-on-demand/` (skill)           | Policy layer. Tells Claude *when* to call the MCP tools, based on command heuristics + project settings.    |
| `hooks/hooks.json` + `teardown.py`        | Stop + SessionEnd safety net. Disconnects the configured profile if Claude forgot to.                       |
| `.claude/openvpn3-on-demand.local.md`     | Per-project settings (user-owned, git-ignored). Declares the profile name, optional provision command, optional extra trigger patterns. |

## Prerequisites

- `openvpn3` CLI installed (Linux: `openvpn3-linux` package; macOS: via Homebrew or openvpn3 sources).
- `python3` (3.10+) on PATH. Used directly by the Stop/SessionEnd teardown hook (stdlib only, no extra packages), and by `uv run --script` for the MCP server.
- `uv` installed (runs the MCP server with inline PEP 723 dependencies). Install from <https://docs.astral.sh/uv/>.

## Install

This plugin ships in the [`aeresov-claude-plugins`](https://github.com/aeresov/claude-plugins) marketplace. Add the marketplace once, then install:

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install openvpn3-on-demand@aeresov-claude-plugins
```

For local development against a working copy:

```bash
claude --plugin-dir /path/to/openvpn3-on-demand
```

## Per-project setup

1. Create `.claude/openvpn3-on-demand.local.md` in your project root:

   ```markdown
   ---
   profile_name: my-prod-vpn
   ovpn_provision_cmd: make get_vpn_client_config OUTPUT=~/.config/openvpn3/my-prod-vpn.ovpn
   trigger_patterns:
     - "mysql .* -h [^ ]*\\.internal"
     - "kubectl --context prod-.*"
   ---

   # Notes for humans go below the frontmatter.
   ```

   See `skills/vpn-on-demand/references/example-local-settings.md` for the
   full commented template.

2. Add the settings file to your project's `.gitignore`:

   ```gitignore
   .claude/*.local.md
   ```

3. If the profile is not yet imported into openvpn3 and `ovpn_provision_cmd`
   is set, the skill will call `vpn_import` automatically on first connect.
   Otherwise, import it yourself once:

   ```bash
   openvpn3 config-import --config /path/to/my-prod-vpn.ovpn \
                          --name my-prod-vpn --persistent
   ```

## How it behaves

- The skill loads when Claude sees a request that plausibly touches a private
  resource (RDS/ElastiCache/MemoryDB hosts, RFC1918 addresses targeted by
  a remote-access verb, `.internal` / `.corp` / `.private` / `.vpc`
  hostnames, `aws` CLI against private services, private `kubectl`
  contexts, etc., plus anything in the project's `trigger_patterns`).
- Claude calls `vpn_connect(profile_name)`. The MCP server runs
  `openvpn3 session-start --config <name>` (or returns early if the session
  already exists).
- Claude runs the user's command. Subsequent VPN-gated commands in the same
  task reuse the tunnel.
- At the end of the task, Claude calls `vpn_disconnect(profile_name)`.
- The Stop and SessionEnd hooks run `teardown.py`, which reads
  `profile_name` from the settings file and disconnects that profile iff
  it's still active. This catches cases where the model forgot step 4.

## MCP tools

| Tool                                    | Args                           | Returns                                                           |
|-----------------------------------------|--------------------------------|-------------------------------------------------------------------|
| `vpn_status`                            | —                              | `{session_count, sessions: [...]}`                                |
| `vpn_connect`                           | `profile_name`                 | `{status: connected / already_connected / error, ...}`            |
| `vpn_disconnect`                        | `profile_name`                 | `{status: disconnected / not_connected / error, ...}`             |
| `vpn_import`                            | `ovpn_path`, `profile_name`    | `{status: imported / already_imported / error, ...}`              |

`profile_name` is required on `vpn_disconnect`; the server will not
disconnect arbitrary sessions it wasn't told about.

## Security notes

- The plugin never reads `.ovpn` contents itself; it hands paths to
  `openvpn3 config-import` and lets openvpn3 own the secret material.
- `.claude/openvpn3-on-demand.local.md` can contain internal hostnames and
  provision commands. Keep it out of git.
- The MCP server refuses to act if `openvpn3` is not on `PATH`. It does not
  try to install it or run as root.
- The teardown hook only targets the profile declared in the settings file;
  unrelated openvpn3 sessions started by other tools are untouched.

## Troubleshooting

- **"openvpn3 CLI not found on PATH"** — install the CLI and restart Claude
  Code. The MCP server process inherits the shell's PATH at startup.
- **`vpn_connect` errors about an unknown config** — the profile isn't
  imported yet. Either set `ovpn_provision_cmd` in the settings file or run
  `openvpn3 config-import` manually.
- **Hook disconnects too aggressively** — the Stop hook fires at every
  turn's end. If your workflow needs the tunnel up for a multi-turn session,
  consider running the commands in one conversation turn, or disable the
  Stop hook locally (keep SessionEnd for safety).
- **Settings changes don't take effect** — edits to
  `.claude/openvpn3-on-demand.local.md` take effect immediately; the
  teardown hook re-reads the file every time it fires, and the skill
  re-reads it per turn. Only changes to the plugin's `hooks.json` or
  `.mcp.json` require restarting Claude Code (those are loaded at session
  start).
