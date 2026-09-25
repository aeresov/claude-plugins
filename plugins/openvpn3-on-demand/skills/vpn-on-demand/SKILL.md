---
name: vpn-on-demand
description: Connect the project's OpenVPN3 tunnel before operations that reach private network resources — RDS/ElastiCache/MemoryDB hosts, internal hostnames, private kubectl contexts, RFC1918 targets of remote-access verbs, targets the project's CLAUDE.md marks as VPN-only (even when a tool hides the host in a config file) — and disconnect at task end. Requires that settings file — without it the skill is a no-op. Linux only. Not for localhost, Docker/compose networks, .local mDNS names, or public endpoints.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" *)
---

# VPN On Demand

Bring the project's OpenVPN3 tunnel up before a command that needs it and down at task end. The plugin's `openvpn3` MCP server provides the tools; this skill decides *when* to call them.

Modes (exactly one in `.claude/openvpn3-on-demand.local.md`):

- **BYO** — `profile_name` names a config the user imported. The plugin starts/stops its sessions and writes the connect overrides into it.
- **Ephemeral** — `ovpn_provision_cmd` prints an `.ovpn` body on stdout. Each VPN-gated turn makes a fresh single-use profile; openvpn3 drops it once the tunnel starts.

## Preflight

1. **Settings file present?** `test -f .claude/openvpn3-on-demand.local.md`. If absent, **stop**: call no `vpn_*` tool, handle the request normally. If the user clearly wants the tunnel, point them at `/openvpn3-on-demand:setup`.
2. **Mode valid?** Exactly one of `profile_name` / `ovpn_provision_cmd` must be present and non-empty in the YAML frontmatter. Otherwise tell the user, call no `vpn_*` tool, proceed without VPN; `/openvpn3-on-demand:setup` rewrites the file.
3. **Tunnel DNS can reach systemd-resolved?** (Once per session, before the first connect.) `test -f /var/lib/openvpn3/netcfg.json` (no sudo). If absent, **stop** and tell the user to run, once per machine:
   ```bash
   sudo openvpn3-admin init-config --write-configs --force
   sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
   sudo killall -INT openvpn3-service-netcfg
   ```
   Without it the tunnel comes up but systemd-resolved never gets the pushed DNS, so private names fail or resolve wrong. Programs must also *ask* systemd-resolved — the [DNS check](#dns-check) covers that. Skip on non-systemd hosts (Alpine etc.); DNS glue there is out of scope.

Optional frontmatter fields (both modes):

- `post_connect_cmd` — run after a fresh connect (not on `already_connected`). Non-fatal.
- `post_disconnect_cmd` — run after a fresh disconnect (not on `not_connected`).
- `config_overrides` — `{name: value}` openvpn3 `config-manage` overrides, passed as `overrides` to `vpn_connect` / `vpn_connect_ephemeral`. They win over the server's `dns-scope=tunnel` baseline (split DNS: only VPN-pushed domains use its DNS, so it coexists with Tailscale, mDNS and other VPNs in systemd-resolved). In BYO mode baseline and overrides persist in the user's profile, also for their own `openvpn3 session-start`; dropping a key here doesn't unset it — if the user asks to remove one, tell them to run `openvpn3 config-manage --config <profile_name> --unset-override <key>`.

Field reference and examples: [`references/example-local-settings.md`](references/example-local-settings.md).

## When to activate

**You decide, per operation.** Nothing watches traffic or knows what sits behind this VPN; the lists below are hints, not a complete detector. Judge where the operation actually connects, not just the command text.

**Activate** when the operation reaches:

- Hosts ending in `.rds.amazonaws.com`, `.elasticache.amazonaws.com`, `.memorydb.amazonaws.com`, `.redshift.amazonaws.com`, `.docdb.amazonaws.com`.
- RFC1918 hosts (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) **targeted by a remote-access verb** (`ssh`, `kubectl`, `mysql`, `psql`, `redis-cli`, `curl` / `wget` to a non-loopback URL). A bare RFC1918 address isn't enough — Docker networks live there too.
- Hostnames ending in `.internal`, `.corp`, `.private`, `.vpc`, or marked internal by the project's CLAUDE.md / README.
- `aws` CLI against private services in prod accounts (RDS, ElastiCache, MemoryDB, Secrets Manager, SSM Parameter Store, ECR or Lambda in a VPC).
- `kubectl` / `helm` against a cluster with a private API endpoint; `ssh` to a host without a public IP.
- Anything the project's CLAUDE.md / README says is VPN-only.

**Hidden targets count.** A DB client reading its host from `--defaults-file`, a login path or `~/.my.cnf` (like `mysql-client` plugin calls), a kubeconfig context, an `ssh` alias, a `make` target or script, a secret-store CLI (Vault, AWS Secrets Manager), any tool or skill reading its endpoint from its own settings. If the project says the target is private, connect before the **first** step that touches it — including a credentials step.

**Do not activate** for local file operations; obviously public endpoints (`github.com`, `pypi.org`, `npmjs.com`, `docker.io`, public APIs); local builds, docs and offline tests; local Docker traffic (`docker0` `172.17.0.0/16`, compose networks, `localhost` / `127.0.0.1` / `::1`); `.local` / mDNS.

Unsure? Check the project's CLAUDE.md / README; if still unsure, ask the user.

**After a failure** (unresolvable host, timeout or refused connection to something possibly private): tunnel down → suspect the VPN before credentials or grants; connect and rerun once if safe to repeat (a read), else ask. Tunnel up → run the [DNS check](#dns-check) on that host first.

## BYO flow

1. **Connect.** `vpn_connect(profile_name=<value>, overrides=<config_overrides or omitted>)`. Idempotent: `already_connected` means the session is up (overrides apply only at tunnel start, so they're skipped); one still connecting is waited for.
2. **Config not imported** (error says "no openvpn3 config named" / "import it first") → **stop** and tell the user: `openvpn3 config-import --config /path/to/file.ovpn --name <profile_name> --persistent`. Never run a provisioning command in this mode.
3. **Post-connect hook** — only on `connected`: run `post_connect_cmd` if set. Non-zero exit: surface it, don't tear down.
4. **[DNS check](#dns-check)** — on `connected` and `already_connected`, per private host.
5. **Run the user's command.** Connect once per VPN-needing block; reuse the tunnel for later VPN-gated commands and don't disconnect between them.
6. **Disconnect at task end.** `vpn_disconnect(profile_name=<value>)`. Idempotent.
7. **Post-disconnect hook** — only on `disconnected`: run `post_disconnect_cmd` if set; failures are informational.

## Ephemeral flow

Profile name `N = "ovpn3-od-" + $CLAUDE_CODE_SESSION_ID`. Pass `$CLAUDE_CODE_SESSION_ID` as `session_id` — the MCP server can't read it (it's a singleton across `/resume`/`/fork-session`). Rebuild `N` only to disconnect. If `CLAUDE_CODE_SESSION_ID` is unset, **stop**: tell the user (Claude Code may be too old), don't guess a name, proceed without VPN.

1. **Provision → connect:**
   1. Run `ovpn_provision_cmd` into the session's fixed path in **one** Bash call, adding per-task values (see "Environment"):
      ```bash
      umask 077
      d="${XDG_RUNTIME_DIR:-$HOME/.cache}/openvpn3-on-demand"; mkdir -p "$d"
      ovpn="$d/$CLAUDE_CODE_SESSION_ID.ovpn"
      if { <provision-cmd> ; } > "$ovpn" && [ -s "$ovpn" ]
      then echo "ovpn=$ovpn"
      else rm -f "$ovpn"; false
      fi
      ```
      The `.ovpn` is sensitive: stdout goes only to the file and must **not** appear in your output (stderr may). The path is fixed per session so later Bash calls can rebuild it; shell variables don't persist.
   2. Non-zero exit → the file is already removed. Show stderr, **stop**.
   3. `vpn_connect_ephemeral(ovpn_path=<the printed ovpn= path>, session_id="$CLAUDE_CODE_SESSION_ID", overrides=<config_overrides or omitted>)` — drops any stale config under `N`, imports single-use, connects atomically.
   4. Delete the file regardless of outcome: `rm -f "${XDG_RUNTIME_DIR:-$HOME/.cache}/openvpn3-on-demand/$CLAUDE_CODE_SESSION_ID.ovpn"`.
2. **Post-connect hook** — only on `connected`, as in BYO.
3. **[DNS check](#dns-check)** — on `connected` and `already_connected`, per private host.
4. **Run the user's command**, reusing the tunnel within the task.
5. **Disconnect at task end.** `vpn_disconnect(profile_name=N)` (or the `profile_name` from the connect response); run `post_disconnect_cmd` on a fresh disconnect.

**Environment.** Keep settings files **task-agnostic**: add per-task values (target environment, AWS profile, region, vault namespace) from the project's CLAUDE.md / README inside the provisioning call — as arguments where the command takes them, `{ make vpn-config OUTPUT=/dev/stdout ENV=dev ; }` (GNU make exports command-line variables to recipes), else as a leading assignment, `{ AWS_PROFILE=acme-dev vault read -field=config secret/vpn ; }`. Never a leading `ENV=…`: like `BASH_ENV` it names a file shells run at startup, so Claude Code refuses it in worktree-isolated sessions. If a provisioner reads `ENV` only from its environment, ask the user to make it an argument.

`ovpn_provision_cmd` runs every VPN-gated turn, even when the server then skips re-import on `already_connected`.

## DNS check

After connecting (`connected` or `already_connected`), check each private host before the first command that connects to it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" --connected '<hostname>'
```

- Pass the bare host. If it's in a URL with a password (`rediss://`, `mysql://`, printed by a command or in a variable), pipe the URL in, in one call: `<command that prints the URL> | python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" --connected --url-stdin` (variable: `printf '%s' "$X_URL" | …`). The script never prints the URL. Never echo, cat or Read anything holding a secret; for a host found only in a credentials file, take it from the project's CLAUDE.md or ask the user. Skip IP-literal targets.
- Check a host learned mid-task (e.g. from a step that fetches a URL) as soon as it's known, before connecting to it. A run without a hostname only reports the resolver setup — it isn't a pass for any host.
- **Exit 0 (OK)** → proceed.
- **Exit 3 (WARN)** → proceed and relay the WARN line. Exception: a WARN saying `/etc/hosts pins it` is a FAIL for clients that follow cluster topology (MemoryDB, ElastiCache cluster mode, `redis-cli -c`, `RedisCluster`) — they connect to node names the pin doesn't cover.
- **Exit 1 (FAIL)** → don't run the command. Show the user the output (what programs and systemd-resolved got, and the fix). The fix needs root — never run it. Ask whether to keep the tunnel up while they apply it (then rerun the check) or disconnect. No `Fix` lines means the FAIL line names the cause (stale `/etc/hosts` pin, hostname, provisioned environment, pushed domains): act on that before asking the user.
- **Exit 2** → arguments rejected (never echoed back); pass one bare host, or the URL via `--url-stdin`, and rerun.

Why: preflight step 3 can pass while programs never ask systemd-resolved (Tailscale, a VPN client or NetworkManager owns `/etc/resolv.conf`), so private names fail or come back `0.0.0.0` — and a client connecting to `0.0.0.0` reaches this machine, where a TLS client can hang with no DNS error. Don't pin `/etc/hosts` or patch the resolver in scripts on your own: each covers one name and goes stale. Offer them only as a stopgap the user chooses.

## Tool reference

All tools return `{"status": ...}`; `status: "error"` is a hard failure — surface the `message`, don't silently retry.

- `vpn_status()` — list active sessions.
- `vpn_connect(profile_name, overrides=None)` — BYO. Idempotent (`already_connected`). `overrides` layer on the `dns-scope=tunnel` baseline (caller wins).
- `vpn_connect_ephemeral(ovpn_path, session_id, overrides=None)` — see [Ephemeral flow](#ephemeral-flow). Response includes the derived `profile_name`.
- `vpn_disconnect(profile_name)` — required; the server won't disconnect arbitrary sessions. Idempotent (`not_connected`).

## Failure modes

Mode, session-ID and provisioning failures: see [Preflight](#preflight) and [Ephemeral flow](#ephemeral-flow).

- **MCP server exited 1 — `cannot import 'dbus' and/or 'openvpn3'`.** Install `openvpn3-client` + `python3-dbus`; restart Claude Code.
- **`"Backend not ready ..."`.** The profile prompts for credentials; the server is non-interactive. Profiles need `auth-user-pass` inlined; encrypted PKCS#12 isn't supported. BYO: re-import a fixed profile. Ephemeral: fix `ovpn_provision_cmd`'s output.
- **`"A session for '<name>' exists but isn't connected ..."` / `"... its backend isn't answering ..."`.** The session predates the call and may be the user's own, so the server leaves it alone. Surface the message (it includes `openvpn3 session-manage --disconnect --config <name>` or `--cleanup`). Don't `vpn_disconnect` it unless the user says so.
- **`"D-Bus error: ..."`.** openvpn3's services are unreachable or denied. It says nothing about whether a tunnel exists — not "not connected", not "config missing". Surface it; suggest `/openvpn3-on-demand:doctor`.
- **Tunnel up (`vpn_status()` shows it) but a private name fails, resolves to `0.0.0.0`, or the command hangs.** Both DNS layers must work: netcfg → systemd-resolved (preflight step 3) and programs → systemd-resolved (the [DNS check](#dns-check)). Run the DNS check on that host (`/openvpn3-on-demand:doctor <hostname>` reports the same). If both pass, look past DNS: routes, security groups, wrong environment's VPN.

## No safety net

Always call `vpn_disconnect` when the VPN-gated block ends — nothing else will. Otherwise the tunnel stays up until the user runs `openvpn3 session-manage --disconnect --config <profile_name>`.
