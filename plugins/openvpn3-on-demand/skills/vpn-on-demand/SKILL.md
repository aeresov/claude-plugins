---
name: vpn-on-demand
description: Connect the project's OpenVPN3 tunnel before commands that touch private network resources — RDS/ElastiCache/MemoryDB hosts, internal hostnames, private kubectl contexts, RFC1918 targets of remote-access verbs, plus any trigger_patterns declared in .claude/openvpn3-on-demand.local.md — and disconnect at task end.
---

# VPN On Demand

Bring up the project's OpenVPN3 tunnel before running any command that needs it, and tear it down when the task is done. The plugin ships an `openvpn3` MCP server with the tools below; this skill is the policy that decides *when* to call them.

## Preflight: is this plugin even active for this project?

This skill only applies when the current project opts in by declaring a VPN profile in `.claude/openvpn3-on-demand.local.md` (see the Configuration section). Before anything else:

1. Check whether `.claude/openvpn3-on-demand.local.md` exists in the project root.
2. If it does not exist, stop — do nothing, do not call any `vpn_*` tool, proceed with the user's request as normal.
3. If it does exist, read it and extract the YAML frontmatter fields:
   - `profile_name` — **required**. The openvpn3 config name for this project.
   - `ovpn_provision_cmd` — optional. Shell command that (re)generates the `.ovpn` file.
   - `trigger_patterns` — optional list of regex strings; extends the built-in trigger defaults.
   - `post_connect_cmd` — optional shell command to run after a fresh `vpn_connect` (see "The core flow").
   - `post_disconnect_cmd` — optional shell command to run after a fresh `vpn_disconnect` (see "The core flow").

If the file exists but `profile_name` is missing or empty, surface that as a configuration error to the user rather than guessing.

## When to activate the VPN

Call `vpn_connect(profile_name)` before executing a command whose destination is a private network resource. Use this matrix:

**Activate** when the command targets:

- Hosts ending in `.rds.amazonaws.com`, `.elasticache.amazonaws.com`, `.memorydb.amazonaws.com`, `.redshift.amazonaws.com`, or `.docdb.amazonaws.com`.
- Hosts in the RFC1918 ranges `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` **only** when targeted by a remote-access verb (`ssh`, `kubectl`, `mysql`, `psql`, `redis-cli`, `curl`/`wget` to a non-loopback URL, etc.). A bare RFC1918 address on its own is not enough — local Docker networks live in this range too.
- Hostnames ending in `.internal`, `.corp`, `.private`, `.vpc`, or anything documented as internal-only in the project's CLAUDE.md / README.
- `aws` CLI calls against prod accounts for services that talk to private endpoints (RDS, ElastiCache, MemoryDB, Secrets Manager, SSM Parameter Store, ECR inside a VPC, Lambda in a VPC).
- `kubectl` / `helm` against a cluster whose API endpoint is private.
- `ssh` to hosts without a public IP.
- Any bash, Read/Grep/Glob-free tool call that matches one of the `trigger_patterns` regexes in the settings file. Patterns there extend the defaults — they do not replace them.

**Do NOT activate** for:

- Read/Edit/Write/Glob/Grep on local files (they never leave the machine).
- Commands hitting obviously public endpoints: `github.com`, `pypi.org`, `npmjs.com`, `docker.io`, public S3 buckets via `https://...s3.amazonaws.com` without a VPC endpoint requirement, public REST APIs.
- Reading docs, running tests that don't hit the network, local builds.
- Local Docker traffic: the `docker0` bridge (`172.17.0.0/16`), `docker compose` project networks, and anything on `localhost` / `127.0.0.1` / `::1`.
- `.local` / mDNS / Bonjour hostnames — those are LAN service discovery, not VPN territory.

When uncertain, check the settings file's `trigger_patterns` and the project's CLAUDE.md for guidance. If still uncertain, ask the user rather than speculating.

## The core flow

Given a matching command, the flow is:

1. **Connect.** Call `vpn_connect(profile_name=<value from settings>)`. The tool is idempotent — if the session already exists, it returns `status: already_connected` immediately.

2. **If connect returns an error about a missing config** (`stderr` mentions "not found" / "no such config" / similar), run first-time provisioning:
   - If the settings file declares `ovpn_provision_cmd`, run that shell command. It is responsible for producing a fresh `.ovpn` file. The command must print the absolute path of the generated file as its last non-empty stdout line, or explicitly set an `OUTPUT=…` path — adopt whichever convention the settings file uses.
   - Call `vpn_config_import(ovpn_path=<path>, profile_name=<name>)` to register it.
   - Retry `vpn_connect(profile_name)`.
   - If `ovpn_provision_cmd` is not set, stop and tell the user: the profile needs to be imported manually (e.g. `openvpn3 config-import --config path/to/file.ovpn --name <name> --persistent`).

3. **Post-connect hook (fresh connects only).** If `vpn_connect` returned `status: connected` (i.e. *not* `already_connected`) and the settings file declares `post_connect_cmd`, run that shell command via Bash before proceeding. Typical uses: warming a DNS cache, probing a VPC endpoint to confirm routing, logging the connection. A non-zero exit is surfaced to the user but **not** fatal — do not tear down the session on post-connect failure. Skip this step when the tunnel was already up; the command is a one-time setup, not a per-turn heartbeat.

4. **Run the user's command.** Proceed as normal.

5. **Disconnect at end of task.** Once the user's task is complete and no subsequent step in the same task still needs VPN, call `vpn_disconnect(profile_name=<value>)`. Disconnecting is also idempotent — `status: not_connected` is fine.

6. **Post-disconnect hook (fresh disconnects only).** If `vpn_disconnect` returned `status: disconnected` (i.e. *not* `not_connected`) and the settings file declares `post_disconnect_cmd`, run that shell command via Bash. Typical uses: flushing DNS resolver caches, tearing down port-forwards set up by `post_connect_cmd`, logging session end. Failures here are informational only. The Stop/SessionEnd safety-net hook *also* runs `post_disconnect_cmd` (with a 5s timeout, silent failure) whenever it actually disconnects a session — so cleanup happens even when the model forgets step 5.

Do not disconnect between two VPN-gated commands in the same task. Connect once, keep the tunnel up for the run, disconnect at the end.

## Resetting a profile (env switch, DNS workaround, stale credentials)

If the user indicates they're switching deployment environments, toggling a DNS workaround, or regenerating the `.ovpn` file for any other reason, the imported openvpn3 config still has the *old* contents — `vpn_connect` would reuse the stale profile. The reset cycle is:

1. `vpn_disconnect(profile_name)` — openvpn3 refuses to remove a config that has an active session, so disconnect first. `status: not_connected` is fine.
2. `vpn_config_remove(profile_name)` — drop the stale import. `status: already_removed` is fine.
3. Next time a VPN-gated command fires, the normal flow (connect → "not imported" → `ovpn_provision_cmd` → `vpn_config_import` → retry connect) re-provisions cleanly.

Do not run the reset cycle speculatively. Only do it when the user asks to switch envs / refresh the profile, or when a connect failure points at a stale config (e.g. auth errors immediately after the user ran their provision target manually).

## Interaction with the teardown hook

The plugin ships Stop and SessionEnd hooks that disconnect the configured profile as a safety net. Those hooks are a backup for the case where the model forgot step 4 above; they are not an excuse to skip it. Explicit disconnect at task end is the expected behavior because:

- Stop also fires between turns in a long conversation; letting the hook do the work means every follow-up turn pays a reconnect.
- Explicit disconnect produces cleaner transcripts (the user sees the intent).

## Tool reference

The MCP server exposes these tools under the `openvpn3` prefix:

- `vpn_status()` — returns the list of active sessions. Useful for confirming state, debugging, or checking before a manual step.
- `vpn_connect(profile_name)` — start a session. Idempotent.
- `vpn_disconnect(profile_name)` — stop a session. Idempotent. `profile_name` is required; the server will not disconnect arbitrary sessions.
- `vpn_config_import(ovpn_path, profile_name)` — register an `.ovpn` file as a named persistent config. Idempotent.
- `vpn_config_remove(profile_name)` — drop an imported config so it can be re-provisioned. Idempotent. Requires the session to be disconnected first.

All tools return a dict with a `status` field. Treat `status: "error"` as a hard failure and surface `stderr` / `stdout` to the user — do not silently retry.

## Configuration file

Per-project settings live in `.claude/openvpn3-on-demand.local.md` (git-ignored). Frontmatter fields:

| Field                 | Required | Purpose                                                                 |
|-----------------------|----------|-------------------------------------------------------------------------|
| `profile_name`        | yes      | Name of the openvpn3 config to start. Matches the argument passed to `vpn_connect` and `vpn_disconnect`. |
| `ovpn_provision_cmd`  | no       | Shell command that (re)generates the `.ovpn` file on first connect. Required only if `vpn_config_import` has never been run for this profile. |
| `trigger_patterns`    | no       | Extra regex patterns to treat as VPN-requiring, beyond the defaults in this skill. |
| `post_connect_cmd`    | no       | Shell command run after a fresh `vpn_connect` (not on `already_connected`). DNS warming, endpoint probes, opening ssh control masters. Non-fatal on failure. |
| `post_disconnect_cmd` | no       | Shell command run after a fresh `vpn_disconnect` (not on `not_connected`). DNS/route cleanup, closing port-forwards. Also runs from the Stop/SessionEnd safety-net hook when it actually disconnects a session (5s timeout, silent failure). |

See `references/example-local-settings.md` for a full commented template.

## Failure modes and how to handle them

- **openvpn3 / dbus-python not installed.** All tools return `{"status": "error", "message": "openvpn3 Python module or dbus-python is not available. ..."}`. Tell the user to install the `openvpn3-client` and `python3-dbus` system packages and stop; do not attempt the command without VPN.
- **Connect fails with auth error.** Surface the `message` to the user. The `.ovpn` file may need re-provisioning or the credentials have rotated. Note: this MCP server is non-interactive — profiles that prompt for a username/password must have credentials embedded (`auth-user-pass` inlined), and profiles that use an encrypted PKCS12 can't be used. A connect that returns `"Backend not ready (likely needs credentials embedded in the profile)"` means the profile asks for interactive input that the server can't supply. This is a `vpn_connect` failure mode, not a `vpn_config_import` one — import just hands the raw file to openvpn3's backend parser.
- **`vpn_status()` shows the session but the command still fails to reach the host.** The tunnel may be up without routing. Confirm with `vpn_status()` and report both the session state and the original command's error — don't just re-run `vpn_connect`.
- **Multiple simultaneous tasks share a profile.** The tunnel is a shared resource. If one task disconnects while another still needs it, the second will fail. Default to connecting at the start of the VPN-requiring block of work and disconnecting only when no further VPN-gated step is queued.

## Additional resources

- `references/example-local-settings.md` — full commented template for the per-project settings file.
