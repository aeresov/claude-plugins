---
name: vpn-on-demand
description: Connect the project's OpenVPN3 tunnel before commands that touch private network resources — RDS/ElastiCache/MemoryDB hosts, internal hostnames, private kubectl contexts, RFC1918 targets of remote-access verbs, plus any trigger_patterns declared in .claude/openvpn3-on-demand.local.md — and disconnect at task end.
---

# VPN On Demand

Bring up the project's OpenVPN3 tunnel before a command that needs it; tear it down at task end. The plugin ships an `openvpn3` MCP server (tools below); this skill is the policy deciding *when* to call them.

Modes (set exactly one in `.claude/openvpn3-on-demand.local.md`):

- **BYO** — `profile_name` names an openvpn3 config the user imported themselves. The plugin only starts/stops sessions for it.
- **Ephemeral** — `ovpn_provision_cmd` is a shell command whose stdout is an `.ovpn` body. A fresh single-use profile is generated each VPN-gated turn; openvpn3 drops it once the tunnel starts.

## Preflight

1. **Settings file present?** `test -f .claude/openvpn3-on-demand.local.md`. If absent, **stop**: call no `vpn_*` tool, handle the request normally. If the user clearly wants the tunnel, point them at `/openvpn3-on-demand:setup`.
2. **Mode valid?** Read the YAML frontmatter. Exactly one of `profile_name` / `ovpn_provision_cmd` must be present and non-empty. Both or neither → tell the user, call no `vpn_*` tool, proceed without VPN. `/openvpn3-on-demand:setup` rewrites the file cleanly.
3. **Host DNS ready?** (Once per session, before the first connect.) `test -f /var/lib/openvpn3/netcfg.json` (no sudo). If absent, **stop** and tell the user to run, once per machine:
   ```bash
   sudo openvpn3-admin init-config --write-configs --force
   sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
   sudo killall -INT openvpn3-service-netcfg
   ```
   Without this the tunnel comes up but `systemd-resolved` never receives the pushed DNS — `*.rds.amazonaws.com` etc. resolve to NXDOMAIN silently. Skip on non-systemd hosts (Alpine etc.) — DNS glue there is out of scope.

Other relevant frontmatter fields (both modes, all optional):

- `trigger_patterns` — extra regex patterns to treat as VPN-requiring, on top of the matrix below.
- `post_connect_cmd` — shell command run after a fresh connect (not on `already_connected`). Non-fatal.
- `post_disconnect_cmd` — shell command run after a fresh disconnect (not on `not_connected`).
- `config_overrides` — `{name: value}` map of openvpn3 `config-manage` overrides. Pass via the `overrides` arg to `vpn_connect` / `vpn_connect_ephemeral`. The server applies `dns-scope=tunnel` as a baseline (split-DNS so the tunnel coexists with Tailscale / mDNS); entries here override on collision.

Full field reference and examples: [`references/example-local-settings.md`](references/example-local-settings.md).

## When to activate

**Activate** when the command targets:

- Hosts ending in `.rds.amazonaws.com`, `.elasticache.amazonaws.com`, `.memorydb.amazonaws.com`, `.redshift.amazonaws.com`, `.docdb.amazonaws.com`.
- RFC1918 hosts (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) **when targeted by a remote-access verb** (`ssh`, `kubectl`, `mysql`, `psql`, `redis-cli`, `curl` / `wget` to a non-loopback URL). A bare RFC1918 address alone isn't enough — local Docker networks live there too.
- Hostnames ending in `.internal`, `.corp`, `.private`, `.vpc`, or anything the project's CLAUDE.md / README marks internal.
- `aws` CLI against private services in prod accounts (RDS, ElastiCache, MemoryDB, Secrets Manager, SSM Parameter Store, ECR in a VPC, Lambda in a VPC).
- `kubectl` / `helm` against a cluster with a private API endpoint.
- `ssh` to a host without a public IP.
- Any command matching `trigger_patterns` in the settings file (these *extend* the defaults).

**Do not activate** for:

- Read/Edit/Write/Glob/Grep on local files.
- Obviously public endpoints (`github.com`, `pypi.org`, `npmjs.com`, `docker.io`, public REST APIs).
- Local builds, docs, tests that don't hit the network.
- Local Docker traffic — `docker0` (`172.17.0.0/16`), compose project networks, `localhost` / `127.0.0.1` / `::1`.
- `.local` / mDNS / Bonjour — LAN service discovery, not VPN territory.

When uncertain, check `trigger_patterns` and the project's CLAUDE.md; if still uncertain, ask the user.

## BYO flow

1. **Connect.** `vpn_connect(profile_name=<value>, overrides=<config_overrides or omitted>)`. Idempotent — `already_connected` returns immediately (overrides skipped; they take effect at tunnel-start time only).
2. **If the config isn't imported** (error message mentions "no openvpn3 config named" / "import it first"), **stop** and tell the user:
   `openvpn3 config-import --config /path/to/file.ovpn --name <profile_name> --persistent`. Don't run any provisioning command in this mode.
3. **Post-connect hook** — only on `status: connected` (not `already_connected`). Run `post_connect_cmd` via Bash if set. Non-zero exit is surfaced but not fatal; do not tear down on failure.
4. **Run the user's command.** Reuse the tunnel for subsequent VPN-gated commands in the task.
5. **Disconnect at task end.** `vpn_disconnect(profile_name=<value>)`. Idempotent.
6. **Post-disconnect hook** — only on `status: disconnected`. Run `post_disconnect_cmd` if set; failures informational.

Connect once at the start of the VPN-needing block, disconnect at the end. Don't disconnect between two VPN-gated commands in the same task.

## Ephemeral flow

Profile name: `N = "ovpn3-od-" + $CLAUDE_CODE_SESSION_ID`. You forward `$CLAUDE_CODE_SESSION_ID` to `vpn_connect_ephemeral` as `session_id`; the MCP server can't read that env var itself (it's a singleton across `/resume`/`/fork-session`). You reconstruct `N` only for disconnect. If `CLAUDE_CODE_SESSION_ID` is unset, **stop**: tell the user (their Claude Code may be too old) and handle the request without VPN.

1. **Provision → connect:**
   1. `tmp="$(mktemp --suffix=.ovpn)"`.
   2. Run `ovpn_provision_cmd` with stdout redirected to `$tmp`, prefixing any per-task env (see "Environment" below) — e.g. `{ ENV=<env> <provision-cmd> ; } > "$tmp"`. The command's stdout bytes must **not** appear in your output (the `.ovpn` is sensitive); stderr may.
   3. Non-zero exit or empty `$tmp` → tell the user (show stderr), `rm -f "$tmp"`, **stop**.
   4. `vpn_connect_ephemeral(ovpn_path=<$tmp>, session_id="$CLAUDE_CODE_SESSION_ID", overrides=<config_overrides or omitted>)`. The server reads the file, drops any stale config under `N`, imports single-use, and connects atomically.
   5. `rm -f "$tmp"` — regardless of outcome.
2. **Post-connect hook** — only on `status: connected`. Same rules as BYO.
3. **Run the user's command.** Reuse the tunnel within the task.
4. **Disconnect at task end.** Reconstruct `N` (or echo `profile_name` from the connect response). `vpn_disconnect(profile_name=N)`. Run `post_disconnect_cmd` on a fresh disconnect.

**Environment.** The provision command inherits the parent process's env. Keep settings files **task-agnostic**: prepend per-task vars (target environment, AWS profile, region, vault namespace) inline from the project's CLAUDE.md / README — `{ ENV=dev AWS_PROFILE=acme-dev <provision-cmd> ; } > "$tmp"`. Baking them into `ovpn_provision_cmd` locks the settings file to one task context.

`ovpn_provision_cmd` runs every VPN-gated turn — by design. The server skips re-import on `already_connected`, but provisioning is paid each turn.

## Tool reference

All tools return `{"status": ...}`; `status: "error"` is a hard failure — surface the `message`, don't silently retry.

- `vpn_status()` — list active sessions.
- `vpn_connect(profile_name, overrides=None)` — BYO. Idempotent (`already_connected`). Server applies `dns-scope=tunnel` baseline; `overrides` entries layer on top (caller wins).
- `vpn_connect_ephemeral(ovpn_path, session_id, overrides=None)` — provision-and-connect. `session_id` comes from `$CLAUDE_CODE_SESSION_ID` in your Bash env (the MCP server can't see that var). Reads the file, drops any stale config under `ovpn3-od-{session_id}`, imports single-use, connects. Response includes the derived `profile_name`.
- `vpn_disconnect(profile_name)` — required; the server won't disconnect arbitrary sessions. Idempotent (`not_connected`).

## Failure modes

- **Misconfigured mode** (both or neither). Surface the config error; call no `vpn_*` tool; proceed without VPN.
- **`CLAUDE_CODE_SESSION_ID` unset** (ephemeral). Tell the user; proceed without VPN; don't guess a name.
- **`ovpn_provision_cmd` failed or produced nothing.** Surface stderr; `rm -f` the temp file; don't connect.
- **MCP server exited 1 — `cannot import 'dbus' and/or 'openvpn3'`.** Install `openvpn3-client` + `python3-dbus`; restart Claude Code.
- **`"Backend not ready ..."`.** Profile prompts for credentials; the server is non-interactive. Profiles need `auth-user-pass` inlined; encrypted PKCS#12 isn't supported. BYO: re-import a fixed profile. Ephemeral: fix `ovpn_provision_cmd`'s output.
- **`vpn_status()` shows the session but the command still can't reach the host.** Tunnel up without DNS — preflight step 3 was skipped. `/openvpn3-on-demand:doctor` flags it.

## No safety net

Always call `vpn_disconnect` when the VPN-gated block ends — nothing else will. If you skip it the tunnel stays up until the user runs `openvpn3 session-manage --disconnect --config <profile_name>` by hand.
