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
| `.claude/openvpn3-on-demand.local.md`     | Per-project settings (user-owned, git-ignored). Picks one mode: `profile_name` (use an openvpn3 config you imported yourself) or `ovpn_provision_cmd` (generate a single-use throwaway config from the command's stdout each turn). Plus optional `trigger_patterns` / `post_connect_cmd` / `post_disconnect_cmd`. |

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

- The Stop and SessionEnd hooks run `teardown.py` as a safety net for when Claude forgot the disconnect step. In BYO mode it disconnects the named profile's session (and leaves the config alone); in ephemeral mode it disconnects `ovpn3-od-<session id>` **and** removes that config. If `post_disconnect_cmd` is set, the hook runs it when it actually disconnects something (5 s timeout, silent failure). The hook touches only the one profile this project is responsible for — never a blanket disconnect.

## MCP tools

| Tool                  | Args                           | Returns                                                           |
|-----------------------|--------------------------------|-------------------------------------------------------------------|
| `vpn_status`          | —                              | `{session_count, sessions: [...]}`                                |
| `vpn_connect`         | `profile_name`                 | `{status: connected / already_connected / error, ...}`            |
| `vpn_disconnect`      | `profile_name`                 | `{status: disconnected / not_connected / error, ...}`             |
| `vpn_config_import`   | `ovpn_path`, `profile_name`, `single_use` (default `false`) | `{status: imported / already_imported / error, ...}` |
| `vpn_config_remove`   | `profile_name`                 | `{status: removed / already_removed / error, ...}`                |

`profile_name` is required on `vpn_disconnect` and `vpn_config_remove`;
the server will not touch sessions or configs it wasn't told about.
`vpn_config_remove` fails if a session is still using the config —
disconnect first. The skill uses this sequence automatically when the
user asks to switch envs or refresh a profile. `single_use=true` imports an ephemeral, memory-only config that openvpn3 drops once a tunnel is started from it — the skill uses it for `ovpn_provision_cmd` profiles.

## Security notes

- The plugin reads the `.ovpn` file only to hand its contents to the openvpn3
  configuration manager over D-Bus (which does the authoritative parsing). In
  ephemeral mode the file is a `mktemp` file (mode 600) holding `ovpn_provision_cmd`'s
  stdout; it exists only between provisioning and import, then is deleted, and its
  bytes are never echoed into the conversation transcript. The secret material lives
  inside openvpn3 after import; the plugin doesn't cache it.
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

## Migrating to 0.5.0

`profile_name` is now optional, but the settings file must declare **exactly one** of `profile_name` / `ovpn_provision_cmd`:

- Had only `profile_name` → no change.
- Had **both** `profile_name` and `ovpn_provision_cmd` → that's now a configuration error. Pick one: drop `ovpn_provision_cmd` and `openvpn3 config-import` the profile yourself once, **or** drop `profile_name` to use ephemeral mode.
- `ovpn_provision_cmd` semantics changed: it must now write the `.ovpn` **contents to stdout**, not write a file to a path it picks / print that path. A command like `make get_vpn_client_config OUTPUT=~/.config/openvpn3/my.ovpn` becomes `make get_vpn_client_config OUTPUT=/dev/stdout`; `aws s3 cp s3://…/vpn.ovpn ~/vpn.ovpn` becomes `aws s3 cp s3://…/vpn.ovpn -`.

## Troubleshooting

- **"openvpn3 Python module or dbus-python is not available"** — install
  both `openvpn3-client` and `python3-dbus` system packages, then restart
  Claude Code so the MCP server process picks them up. If the error
  persists after installing those packages (this affected 0.4.0–0.4.2),
  the launcher's venv is pinned to a uv-managed Python that can't see
  /usr/lib/python3/dist-packages: upgrade to ≥0.4.3 and the next launch
  will recreate the venv against /usr/bin/python3 automatically. As a
  manual workaround on older versions, delete
  `servers/openvpn3/.venv` under the plugin directory and restart.
- **`vpn_connect` errors about an unknown config** — in BYO mode the profile
  isn't imported; run `openvpn3 config-import --name <profile_name> --persistent`.
  In ephemeral mode this normally just means the skill needs to (re)provision —
  check that `ovpn_provision_cmd` is set and that it writes a valid `.ovpn` to
  stdout. If ephemeral connects fail before any tool runs, check that
  `CLAUDE_CODE_SESSION_ID` is set in the environment (very old Claude Code
  versions don't set it; the skill declines ephemeral mode without it).
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
