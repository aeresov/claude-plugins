# openvpn3-on-demand

A Claude Code plugin that raises an OpenVPN3 tunnel on demand — right before
the model runs a command that needs private network access — and tears it
down at session end as a safety net. No always-on VPN, no per-command
`Bash(openvpn3 *)` permission grants.

Linux-only. Talks to `openvpn3-linux` over its D-Bus services via the
`openvpn3` Python module shipped with `openvpn3-client`.

## Components

| Piece                                     | What it does                                                                                                |
|-------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| `servers/openvpn3/` (MCP server, uv project) | Exposes `vpn_connect`, `vpn_disconnect`, `vpn_status`, `vpn_config_import`, `vpn_config_remove` to Claude via `.mcp.json`. Calls openvpn3 over D-Bus; no CLI shell-out. The launcher script recreates the venv with `--system-site-packages` the first time so it can see system-installed `dbus` and `openvpn3` modules. |
| `skills/vpn-on-demand/` (skill)           | Policy layer. Tells Claude *when* to call the MCP tools, based on command heuristics + project settings.    |
| `hooks/hooks.json` + `teardown.py`        | Stop + SessionEnd safety net. Disconnects the configured profile if Claude forgot to. Uses the same D-Bus API as the MCP server; silently no-ops if `python3-dbus`/`openvpn3-client` aren't installed. |
| `.claude/openvpn3-on-demand.local.md`     | Per-project settings (user-owned, git-ignored). Declares the profile name, optional provision command, optional extra trigger patterns. |

## Prerequisites

- `openvpn3-client` system package installed (Debian/Ubuntu: `apt install openvpn3-client`; Fedora/RHEL: the upstream OpenVPN 3 repo). Provides the D-Bus services *and* the `openvpn3` Python module at `/usr/lib/python3/dist-packages/openvpn3/`.
- `python3-dbus` system package. Debian/Ubuntu: `apt install python3-dbus`. This is NOT automatically pulled in by `openvpn3-client` (it should be, but the package only declares a bare `python3:any` dep), though it is usually present transitively on desktop installs. Minimal servers/containers need to install it explicitly.
- `python3` (3.10+) on PATH. Used both by the MCP server venv and directly by the Stop/SessionEnd teardown hook.
- `uv` installed. Runs the MCP server via `scripts/launch.sh`; on first use the launcher creates `.venv/` with `--system-site-packages` and syncs `mcp` into it. Install from <https://docs.astral.sh/uv/>.

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
   is set, the skill will call `vpn_config_import` automatically on first connect.
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
- Claude calls `vpn_connect(profile_name)`. The MCP server asks the
  openvpn3 session manager to start a tunnel for the named config (or
  returns early if the session already exists).
- On a fresh connect, if the settings file declares `post_connect_cmd`,
  Claude runs it — typical uses are warming a DNS cache, probing a VPC
  endpoint, or opening an ssh control master. Non-fatal on failure.
- Claude runs the user's command. Subsequent VPN-gated commands in the same
  task reuse the tunnel.
- At the end of the task, Claude calls `vpn_disconnect(profile_name)`; on a
  fresh disconnect, `post_disconnect_cmd` (if set) runs for cleanup.
- The Stop and SessionEnd hooks run `teardown.py`, which reads
  `profile_name` from the settings file and disconnects that profile iff
  it's still active. This catches cases where the model forgot step 4.
  If `post_disconnect_cmd` is set, the hook also runs it (5s timeout,
  silent failure) — so DNS/route cleanup still happens on the safety-net
  path.

## MCP tools

| Tool                  | Args                           | Returns                                                           |
|-----------------------|--------------------------------|-------------------------------------------------------------------|
| `vpn_status`          | —                              | `{session_count, sessions: [...]}`                                |
| `vpn_connect`         | `profile_name`                 | `{status: connected / already_connected / error, ...}`            |
| `vpn_disconnect`      | `profile_name`                 | `{status: disconnected / not_connected / error, ...}`             |
| `vpn_config_import`   | `ovpn_path`, `profile_name`    | `{status: imported / already_imported / error, ...}`              |
| `vpn_config_remove`   | `profile_name`                 | `{status: removed / already_removed / error, ...}`                |

`profile_name` is required on `vpn_disconnect` and `vpn_config_remove`;
the server will not touch sessions or configs it wasn't told about.
`vpn_config_remove` fails if a session is still using the config —
disconnect first. The skill uses this sequence automatically when the
user asks to switch envs or refresh a profile.

## Security notes

- The plugin reads the `.ovpn` file only to pass it to `openvpn3.ConfigParser`,
  which inlines any external cert/key references and hands the result to the
  openvpn3 configuration manager over D-Bus. The secret material lives inside
  openvpn3 after import; the plugin doesn't cache it.
- `.claude/openvpn3-on-demand.local.md` can contain internal hostnames and
  provision commands. Keep it out of git.
- The MCP server refuses to act if the `openvpn3` Python module or
  `dbus-python` is missing. It does not try to install them or run as root.
- The teardown hook only targets the profile declared in the settings file;
  unrelated openvpn3 sessions started by other tools are untouched.

## Licensing

AGPL-3.0-only. The plugin embeds calls to the AGPL-licensed `openvpn3`
Python module that ships with `openvpn3-client`; the whole repo is under
the same license for consistency. See [LICENSE](../../LICENSE) at the repo
root for the full text.

## Troubleshooting

- **"openvpn3 Python module or dbus-python is not available"** — install
  both `openvpn3-client` and `python3-dbus` system packages, then restart
  Claude Code so the MCP server process picks them up.
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
