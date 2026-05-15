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

1. Check whether `.claude/openvpn3-on-demand.local.md` exists in the project root. If not, **stop** — do nothing, call no `vpn_*` tool, handle the user's request normally. (If the user seems to actually want the tunnel, tell them the plugin isn't configured for this project and point them at `/openvpn3-on-demand:setup` — or offer to run it for them.)
2. If it exists, read its YAML frontmatter. Relevant fields:
   - `profile_name` — selects BYO mode.
   - `ovpn_provision_cmd` — selects ephemeral mode.
   - `trigger_patterns` — optional list of regex strings; extends the built-in triggers below. (Both modes.)
   - `post_connect_cmd` — optional shell command run after a fresh `vpn_connect`. (Both modes.)
   - `post_disconnect_cmd` — optional shell command run after a fresh `vpn_disconnect`. (Both modes.)
   - `config_overrides` — optional `{name: value}` map of openvpn3 `config-manage` overrides applied to the configuration before each tunnel start. Names are the hyphenated openvpn3 override names (`dns-scope`, `persist-tun`, `log-level`, …). Pass it to `vpn_connect` via the `overrides` arg. (Both modes.)
3. **Validate the mode.** Exactly one of `profile_name` / `ovpn_provision_cmd` must be present and non-empty:
   - Neither → tell the user the settings file must declare either `profile_name` (an already-imported config) or `ovpn_provision_cmd` (an ephemeral one).
   - Both → tell the user the two fields are mutually exclusive — `profile_name` for an existing config, `ovpn_provision_cmd` for an ephemeral one.
   In either error case: surface the message, call **no** `vpn_*` tool, and proceed with the user's request as normal (their command may fail if it needed the tunnel — that's the misconfiguration's fault, not something to paper over). Re-running `/openvpn3-on-demand:setup` rewrites the settings file cleanly; `/openvpn3-on-demand:doctor` shows the full state.
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

1. **Connect.** `vpn_connect(profile_name=<value>, overrides=<config_overrides or omitted>)`. Idempotent — `already_connected` returns immediately (overrides are skipped on `already_connected`; they take effect at tunnel-start time, so reapplying them to a live session does nothing).
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
2. **Try connecting first.** `vpn_connect(profile_name=N, overrides=<config_overrides or omitted>)`.
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
   7. `vpn_connect(profile_name=N, overrides=<config_overrides or omitted>)`. On `status: connected`, run the post-connect hook (step 4). On `status: error`, surface the `message` and stop.
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
- `vpn_connect(profile_name, overrides=None)` — start a session. Idempotent (`already_connected`). Optional `overrides`: `{name: value}` map of openvpn3 config-manage overrides (e.g. `{"dns-scope": "tunnel"}`) applied to the configuration before NewTunnel; skipped on `already_connected`. Connected payload echoes them as `overrides_applied`.
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
| `config_overrides` | both | no | YAML map of openvpn3 `config-manage` overrides (hyphenated names: `dns-scope`, `persist-tun`, `log-level`, …) reapplied before each tunnel start. Canonical use: `dns-scope: tunnel` for clean split-DNS coexistence with another resolver (Tailscale's MagicDNS, corporate DNS). |

Exactly one of `profile_name` / `ovpn_provision_cmd` — setting both, or neither, is a configuration error (see Preflight). See `references/example-local-settings.md` for full commented templates.

## Failure modes and how to handle them

- **Misconfigured mode** (both, or neither, of `profile_name` / `ovpn_provision_cmd`). Surface the config error to the user; call no `vpn_*` tool; proceed without VPN.
- **`CLAUDE_CODE_SESSION_ID` unset (ephemeral mode).** Tell the user; proceed without VPN; do not guess a name.
- **`ovpn_provision_cmd` fails or emits nothing.** Surface its stderr; `rm -f` the temp file; do not connect.
- **openvpn3 / dbus-python not installed.** All tools return `{"status": "error", "message": "openvpn3 Python module or dbus-python is not available. ..."}`. Tell the user to install the `openvpn3-client` and `python3-dbus` system packages; stop. (`/openvpn3-on-demand:doctor` checks both, plus the netcfg init.)
- **Connect fails with an auth error, or `"Backend not ready (likely needs credentials embedded in the profile)"`.** The MCP server is non-interactive — profiles that prompt for a username/password must have `auth-user-pass` inlined; encrypted PKCS#12 can't be used. Surface the `message`. In BYO mode the user re-imports a fixed profile; in ephemeral mode fix `ovpn_provision_cmd`'s output.
- **`vpn_status()` shows the session but the command still can't reach the host.** Tunnel up without routing/DNS — confirm with `vpn_status()` and report both the session state and the original command's error; don't just re-run `vpn_connect`. Usually the host netcfg init (preflight step 4) was skipped — `/openvpn3-on-demand:doctor` flags that.
- **Multiple simultaneous tasks share a profile.** The tunnel is a shared resource. Connect at the start of the VPN-requiring block of work and disconnect only when no further VPN-gated step is queued.

## Additional resources

- `references/example-local-settings.md` — full commented templates for both modes.
- `/openvpn3-on-demand:setup` — interactive configurator: picks BYO vs ephemeral, writes `.claude/openvpn3-on-demand.local.md`, adds it to `.gitignore`. `/openvpn3-on-demand:doctor` — read-only health check (host packages, netcfg init, settings file present + valid, BYO profile imported, `.gitignore`).
