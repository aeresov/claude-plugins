---
name: vpn-on-demand
description: Connect the project's OpenVPN tunnel (openvpn3-linux, or OpenVPN 2 on hosts without it) before operations that reach private network resources — RDS/ElastiCache/MemoryDB hosts, internal hostnames, private kubectl contexts, RFC1918 targets of remote-access verbs, targets the project's AGENTS.md / CLAUDE.md marks as VPN-only (even when a tool hides the host in a config file) — and disconnect at task end. Requires that settings file — without it the skill is a no-op. Linux only. Not for localhost, Docker/compose networks, .local mDNS names, or public endpoints.
---

# VPN On Demand


## Client compatibility

This skill supports Claude Code and Codex. Resolve `../..` from this SKILL.md's directory to the absolute plugin root and substitute it for `<plugin-root>` below. Keep shell calls in the consuming project directory. Both clients intentionally share the gitignored `.claude/` settings files; no settings migration is needed.

Bring the project's VPN tunnel up before a command that needs it and down at task end. The plugin's `openvpn` MCP server provides the tools; this skill decides *when* to call them.

Modes (exactly one in `.claude/openvpn-on-demand.local.md`):

- **BYO** — `profile_name` names a config the user imported. The plugin starts/stops its sessions and (openvpn3) writes the connect overrides into it.
- **Ephemeral** — `ovpn_provision_cmd` prints an `.ovpn` body on stdout. Each VPN-gated turn makes a fresh single-use profile; it is gone once the tunnel starts (openvpn3) or stops (openvpn2).

**Backends.** The server uses openvpn3-linux where it is installed, else the distribution's OpenVPN 2.6 (Amazon Linux 2023 and other hosts with no openvpn3 package). `vpn_status()` and every connect result name it in `backend`: `openvpn3` or `openvpn2`. The flows below are the same on both; where they differ, this skill says so. On `openvpn2`:

- DNS is left alone. The tunnel only adds routes to private ranges; names resolve as they do without it. The [DNS check](#dns-check) still runs and knows this.
- The profile must pass an allowlist (no scripts, plugins, log files or routes of its own). A refusal names the line; relay it, don't edit the profile yourself.
- `config_overrides` are not applied; the connect result's `notes` says so.
- A tunnel closes by itself after 60 min with next to no traffic, after 12 h, or when the VPN server stops answering. So connect again at the start of each VPN-gated block — the connect tools are idempotent.
- Read the connect result's `notes` (only a fresh `connected` has them; on `already_connected` the same lists are in `/run/openvpn-on-demand/<profile>.ok`): they list the routes the tunnel carries, any route the VPN pushed that was left out (not a private range, or inside a network this host is itself attached to — targets there are not reachable through this tunnel), and any route another tunnel or the host already had for the same prefix (that one keeps the traffic).

## Preflight

1. **Settings file present?** `test -f .claude/openvpn-on-demand.local.md`. If absent, **stop**: call no `vpn_*` tool, handle the request normally. If the user clearly wants the tunnel, point them at `openvpn-on-demand-setup`.
2. **Mode valid?** Exactly one of `profile_name` / `ovpn_provision_cmd` must be present and non-empty in the YAML frontmatter. Otherwise tell the user, call no `vpn_*` tool, proceed without VPN; `openvpn-on-demand-setup` rewrites the file.
3. **Tunnel DNS can reach systemd-resolved?** (Once per session, before the first connect; **openvpn3 backend only**.) Call `vpn_status()` first: if its `backend` is `openvpn2`, skip this step — that backend doesn't touch DNS. Otherwise `test -f /var/lib/openvpn3/netcfg.json` (no sudo). If absent, **stop** and tell the user to run, once per machine:
   ```bash
   sudo openvpn3-admin init-config --write-configs --force
   sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
   sudo killall -INT openvpn3-service-netcfg
   ```
   Without it the tunnel comes up but systemd-resolved never gets the pushed DNS, so private names fail or resolve wrong. Programs must also *ask* systemd-resolved — the [DNS check](#dns-check) covers that. Skip on non-systemd hosts (Alpine etc.); DNS glue there is out of scope.

Optional frontmatter fields (both modes):

- `post_connect_cmd` — run after a fresh connect (not on `already_connected`). Non-fatal.
- `post_disconnect_cmd` — run after a fresh disconnect (not on `not_connected`).
- `config_overrides` — openvpn3 only (pass them anyway; the openvpn2 backend reports them as not applied). `{name: value}` openvpn3 `config-manage` overrides, passed as `overrides` to `vpn_connect` / `vpn_connect_ephemeral`. They win over the server's `dns-scope=tunnel` baseline (split DNS: only VPN-pushed domains use its DNS, so it coexists with Tailscale, mDNS and other VPNs in systemd-resolved). In BYO mode baseline and overrides persist in the user's profile, also for their own `openvpn3 session-start`; dropping a key here doesn't unset it — if the user asks to remove one, tell them to run `openvpn3 config-manage --config <profile_name> --unset-override <key>`.

Field reference and examples: [`references/example-local-settings.md`](references/example-local-settings.md).

## When to activate

**You decide, per operation.** Nothing watches traffic or knows what sits behind this VPN; the lists below are hints, not a complete detector. Judge where the operation actually connects, not just the command text.

**Activate** when the operation reaches:

- Hosts ending in `.rds.amazonaws.com`, `.elasticache.amazonaws.com`, `.memorydb.amazonaws.com`, `.redshift.amazonaws.com`, `.docdb.amazonaws.com`.
- RFC1918 hosts (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) **targeted by a remote-access verb** (`ssh`, `kubectl`, `mysql`, `psql`, `redis-cli`, `curl` / `wget` to a non-loopback URL). A bare RFC1918 address isn't enough — Docker networks live there too.
- Hostnames ending in `.internal`, `.corp`, `.private`, `.vpc`, or marked internal by the project's AGENTS.md / CLAUDE.md / README.
- `aws` CLI against private services in prod accounts (RDS, ElastiCache, MemoryDB, Secrets Manager, SSM Parameter Store, ECR or Lambda in a VPC).
- `kubectl` / `helm` against a cluster with a private API endpoint; `ssh` to a host without a public IP.
- Anything the project's AGENTS.md / CLAUDE.md / README says is VPN-only.

**Hidden targets count.** A DB client reading its host from `--defaults-file`, a login path or `~/.my.cnf` (like `mysql-client` plugin calls), a kubeconfig context, an `ssh` alias, a `make` target or script, a secret-store CLI (Vault, AWS Secrets Manager), any tool or skill reading its endpoint from its own settings. If the project says the target is private, connect before the **first** step that touches it — including a credentials step.

**Do not activate** for local file operations; obviously public endpoints (`github.com`, `pypi.org`, `npmjs.com`, `docker.io`, public APIs); local builds, docs and offline tests; local Docker traffic (`docker0` `172.17.0.0/16`, compose networks, `localhost` / `127.0.0.1` / `::1`); `.local` / mDNS.

Unsure? Check the project's AGENTS.md / CLAUDE.md / README; if still unsure, ask the user.

**After a failure** (unresolvable host, timeout or refused connection to something possibly private): tunnel down → suspect the VPN before credentials or grants; connect and rerun once if safe to repeat (a read), else ask. Tunnel up → run the [DNS check](#dns-check) on that host first.

## BYO flow

1. **Connect.** `vpn_connect(profile_name=<value>, overrides=<config_overrides or omitted>)`. Idempotent: `already_connected` means the session is up (overrides apply only at tunnel start, so they're skipped); one still connecting is waited for.
2. **Config not imported** (error says "no openvpn3 config named" / "import it first") → **stop** and tell the user how to import it — openvpn3: `openvpn3 config-import --config /path/to/file.ovpn --name <profile_name> --persistent`; openvpn2: the `sudo install … /etc/openvpn/client/<profile_name>.conf` line the error carries. Never run a provisioning command in this mode.
3. **Post-connect hook** — only on `connected`: run `post_connect_cmd` if set. Non-zero exit: surface it, don't tear down.
4. **[DNS check](#dns-check)** — on `connected` and `already_connected`, per private host.
5. **Run the user's command.** Connect once per VPN-needing block; reuse the tunnel for later VPN-gated commands and don't disconnect between them.
6. **Disconnect at task end.** `vpn_disconnect(profile_name=<value>)`. Idempotent.
7. **Post-disconnect hook** — only on `disconnected`: run `post_disconnect_cmd` if set; failures are informational.

## Ephemeral flow

Profile name `N = "ovpn-od-" + task_id`. Use the host's session/thread identifier when present (`CLAUDE_CODE_SESSION_ID` or `CODEX_THREAD_ID`), otherwise generate a UUID once for this VPN-gated block. Retain the printed `task_id` and `ovpn` path and pass the identifier literally as the MCP `session_id` argument. The MCP process cannot be assumed to inherit the chat's identity. Reuse the same identifier for the whole block, including delegated work; never use a shared `default` identifier.

1. **Provision → connect:**
   1. Run `ovpn_provision_cmd` into the session's fixed path in **one** shell call, adding per-task values (see "Environment"):
      ```bash
      umask 077
      d="${XDG_RUNTIME_DIR:-$HOME/.cache}/openvpn-on-demand"; mkdir -p "$d"
      task_id="${CLAUDE_CODE_SESSION_ID:-${CODEX_THREAD_ID:-}}"
      if [ -z "$task_id" ]; then task_id=$(python3 -c 'import uuid; print(uuid.uuid4())') || exit 1; fi
      case "$task_id" in *[!a-zA-Z0-9_-]*) echo 'Invalid VPN task identifier' >&2; exit 1 ;; esac
      ovpn="$d/$task_id.ovpn"
      if { <provision-cmd> ; } > "$ovpn" && [ -s "$ovpn" ]
      then printf 'task_id=%s\novpn=%s\n' "$task_id" "$ovpn"
      else : > "$ovpn"; printf 'provisioning failed; emptied ovpn=%s\n' "$ovpn" >&2; false
      fi
      ```
      The `.ovpn` is sensitive: stdout goes only to the file and must **not** appear in your output (stderr may). Retain the printed literal path and identifier for later calls; shell variables don't persist. If re-provisioning while the tunnel is already up, reuse the retained identifier instead of generating another one.
   2. Non-zero exit → the file has been emptied, not removed: Claude Code's built-in safety check refuses an `rm` whose path is built from shell variables unless a person approves it, which nobody can in an unattended or remote session. Remove it by the literal path stderr printed (`rm -f '<the literal ovpn= path>'`), show stderr, **stop**.
   3. `vpn_connect_ephemeral(ovpn_path=<the printed ovpn= path>, session_id=<the printed task_id>, overrides=<config_overrides or omitted>)` — drops any stale config under `N`, imports single-use, connects atomically. Pass the literal values, not shell variable expressions.
   4. Delete the exact file regardless of outcome: `rm -f '<the literal ovpn= path printed above>'`.
2. **Post-connect hook** — only on `connected`, as in BYO.
3. **[DNS check](#dns-check)** — on `connected` and `already_connected`, per private host.
4. **Run the user's command**, reusing the tunnel within the task.
5. **Disconnect at task end.** `vpn_disconnect(profile_name=N)` (or the `profile_name` from the connect response); run `post_disconnect_cmd` on a fresh disconnect.

**Environment.** Keep settings files **task-agnostic**: add per-task values (target environment, AWS profile, region, vault namespace) from the project's AGENTS.md / CLAUDE.md / README inside the provisioning call — as arguments where the command takes them, `{ make vpn-config OUTPUT=/dev/stdout ENV=dev ; }` (GNU make exports command-line variables to recipes), else as a leading assignment, `{ AWS_PROFILE=acme-dev vault read -field=config secret/vpn ; }`. Never a leading `ENV=…`: like `BASH_ENV` it names a file shells run at startup, so Claude Code refuses it in worktree-isolated sessions. If a provisioner reads `ENV` only from its environment, ask the user to make it an argument.

`ovpn_provision_cmd` runs every VPN-gated turn, even when the server then skips re-import on `already_connected`.

## DNS check

After connecting (`connected` or `already_connected`), check each private host before the first command that connects to it:

```bash
python3 "<plugin-root>/servers/openvpn/src/openvpn_mcp/dnscheck.py" --connected --backend <backend> '<hostname>'
```

`<backend>` is the `backend` value of the connect result (`openvpn3` or `openvpn2`); it tells the check whether the tunnel's DNS is in systemd-resolved at all.

- Pass the bare host. If it's in a URL with a password (`rediss://`, `mysql://`, printed by a command or in a variable), pipe the URL in, in one call: `<command that prints the URL> | python3 "<plugin-root>/servers/openvpn/src/openvpn_mcp/dnscheck.py" --connected --backend <backend> --url-stdin` (variable: `printf '%s' "$X_URL" | …`). The script never prints the URL. Never echo, cat or Read anything holding a secret; for a host found only in a credentials file, take it from the project's AGENTS.md / CLAUDE.md or ask the user. Skip IP-literal targets.
- Check a host learned mid-task (e.g. from a step that fetches a URL) as soon as it's known, before connecting to it. A run without a hostname only reports the resolver setup — it isn't a pass for any host.
- **Exit 0 (OK)** → proceed.
- **Exit 3 (WARN)** → proceed and relay the WARN line. Exception: a WARN saying `/etc/hosts pins it` is a FAIL for clients that follow cluster topology (MemoryDB, ElastiCache cluster mode, `redis-cli -c`, `RedisCluster`) — they connect to node names the pin doesn't cover.
- **Exit 1 (FAIL)** → don't run the command. Show the user the output (what programs and systemd-resolved got, and the fix). The fix needs root — never run it. On the openvpn2 backend there is no fix to print: the name has to resolve to a private address through the host's own resolver, and a FAIL means it doesn't (wrong name, wrong environment, or a name only the private network's DNS knows) — or that the address is one this host reaches directly, outside the tunnel (it lies in the host's own network), which no setting on this host changes. Ask whether to keep the tunnel up while they apply it (then rerun the check) or disconnect. No `Fix` lines means the FAIL line names the cause (stale `/etc/hosts` pin, hostname, provisioned environment, pushed domains): act on that before asking the user.
- **Exit 2** → arguments rejected (never echoed back); pass one bare host, or the URL via `--url-stdin`, and rerun.

Why (openvpn3): preflight step 3 can pass while programs never ask systemd-resolved (Tailscale, a VPN client or NetworkManager owns `/etc/resolv.conf`), so private names fail or come back `0.0.0.0` — and a client connecting to `0.0.0.0` reaches this machine, where a TLS client can hang with no DNS error. Don't pin `/etc/hosts` or patch the resolver in scripts on your own: each covers one name and goes stale. Offer them only as a stopgap the user chooses.

## Tool reference

All tools return `{"status": ...}`; `status: "error"` is a hard failure — surface the `message`, don't silently retry.

- `vpn_status()` — list active sessions; `backend` says which VPN client this host uses.
- `vpn_connect(profile_name, overrides=None)` — BYO. Idempotent (`already_connected`). `overrides` layer on the `dns-scope=tunnel` baseline (caller wins); openvpn3 only. A `connected` result may carry `notes`.
- `vpn_connect_ephemeral(ovpn_path, session_id, overrides=None)` — see [Ephemeral flow](#ephemeral-flow). Response includes the derived `profile_name`.
- `vpn_disconnect(profile_name)` — required; the server won't disconnect arbitrary sessions. Idempotent (`not_connected`).

## Failure modes

Mode, session-ID and provisioning failures: see [Preflight](#preflight) and [Ephemeral flow](#ephemeral-flow).

- **MCP server exited 1 — `no VPN backend found`.** Neither client is usable: install `openvpn3-client` + `python3-dbus`, or the distribution's `openvpn` 2.6 package (`sudo dnf install openvpn` on Amazon Linux 2023); restart the client. `openvpn-on-demand-doctor` says which is missing.
- **MCP server exited 1 — `openvpn3 is installed, but 'dbus' and/or 'openvpn3' can't be imported`.** An openvpn3 host whose Python modules don't load: the user installs `python3-dbus` (`openvpn3-client` doesn't pull it in) and restarts the client; `openvpn-on-demand-doctor` check 2 confirms. Installing `openvpn` does not help there — on a host with openvpn3 the server uses OpenVPN 2 only when `OPENVPN_OD_BACKEND=openvpn2` is set.
- **`"Backend not ready ..."`** (openvpn3). The profile prompts for credentials; the server is non-interactive. Profiles need `auth-user-pass` inlined; encrypted PKCS#12 isn't supported. BYO: re-import a fixed profile. Ephemeral: fix `ovpn_provision_cmd`'s output.
- **`"A session for '<name>' exists but isn't connected ..."` / `"... its backend isn't answering ..."` / `"A tunnel for '<name>' exists but isn't connected ..."`.** The session predates the call and may be the user's own, so the server leaves it alone. Surface the message (it includes the command that clears it: `openvpn3 session-manage --disconnect --config <name>` or `--cleanup`; on openvpn2 `sudo systemctl stop <unit>`). Don't `vpn_disconnect` it unless the user says so.
- **`"D-Bus error: ..."`.** openvpn3's services are unreachable or denied. It says nothing about whether a tunnel exists — not "not connected", not "config missing". Surface it; suggest `openvpn-on-demand-doctor`.
- **`"OpenVPN 2 backend: ..."`.** The same for openvpn2: sudo, systemd or the helper didn't answer — it says nothing about whether a tunnel exists. If it mentions `sudo -n`, the user has to allow the helper (the message and `openvpn-on-demand-doctor` check 2 say how); never run `sudo` yourself to work around it.
- **`"profile line N: directive '…' is not allowed ..."`** (openvpn2). The profile holds something the backend won't hand to a root-run OpenVPN. BYO: the user fixes the file. Ephemeral: `ovpn_provision_cmd`'s output needs fixing. Don't strip lines yourself.
- **`"Connect failed: ..."` / `"Connect did not finish ..."`** (openvpn2). The message carries OpenVPN's last log lines and usually a one-sentence reason. An endpoint that doesn't answer or whose name doesn't resolve may simply be off: check the project's AGENTS.md / CLAUDE.md for a step that brings the VPN endpoint up first (some projects keep it down to save cost), run it or ask, then connect again.
- **`"The tunnel came up and was taken down again ..."`** (openvpn2). The VPN handed the tunnel an address that isn't private, that belongs to this host's gateway, DNS server or metadata address, or whose network is a piece of one this host is attached to; or the tunnel changed how one of those addresses is reached; or IPv6 could not be switched off on its device. The host is fine; the tunnel is gone. Surface it — the VPN's configuration and this host don't fit, and that is the user's call. The same goes for `"... could not be read from the journal ..."` (the helper reads the pushed routes there) and `"... could not be checked ..."`.
- **Tunnel up (`vpn_status()` shows it) but a private name fails, resolves to `0.0.0.0`, or the command hangs.** openvpn3: both DNS layers must work: netcfg → systemd-resolved (preflight step 3) and programs → systemd-resolved (the [DNS check](#dns-check)). Run the DNS check on that host (`openvpn-on-demand-doctor <hostname>` reports the same). If it passes, look past DNS: routes (openvpn2: is the address inside the routes the connect result's `notes` listed, and not inside this host's own subnet?), security groups, wrong environment's VPN.

## No safety net

Always call `vpn_disconnect` when the VPN-gated block ends. On openvpn3 nothing else will: the tunnel stays up until the user runs `openvpn3 session-manage --disconnect --config <profile_name>`. On openvpn2 a forgotten tunnel closes itself (60 min with next to no traffic, 12 h at most), or the user stops it with `sudo systemctl stop openvpn-on-demand-<profile_name>.service` — a backstop, not a reason to skip the disconnect.
