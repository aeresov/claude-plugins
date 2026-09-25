# openvpn3-on-demand

A Claude Code plugin that brings an OpenVPN3 tunnel up and down on demand — no always-on VPN, no `Bash(openvpn3 *)` permission grants.
**Linux only.** Talks to `openvpn3-linux` over D-Bus via the `openvpn3` Python module from `openvpn3-client`.

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install openvpn3-on-demand@aeresov-claude-plugins
/openvpn3-on-demand:setup     # writes .claude/openvpn3-on-demand.local.md + .gitignore
```

Then ask Claude for something that hits a private resource; the skill connects first. See [When does the tunnel come up?](#when-does-the-tunnel-come-up). `/openvpn3-on-demand:doctor` is a read-only health check.

## Prerequisites

- `openvpn3-client` — D-Bus services and the `openvpn3` Python module (Debian/Ubuntu: `apt install openvpn3-client`).
- `python3-dbus` — not pulled in by `openvpn3-client` (Debian/Ubuntu: `apt install python3-dbus`).
- `python3` ≥ 3.10 and [`uv`](https://docs.astral.sh/uv/) (runs the MCP server) on `PATH`.
- One-time netcfg init, so the tunnel's DNS reaches systemd-resolved:
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```
- Programs resolving through systemd-resolved: `/etc/resolv.conf` is the stub (Ubuntu's default) or `libnss-resolve` is installed. Tailscale, VPN clients and NetworkManager can take that file over — see [Troubleshooting](#tunnel-up-but-private-names-dont-resolve).

`/openvpn3-on-demand:doctor` checks all of this except the Python version. With the tunnel up, `/openvpn3-on-demand:doctor <private-hostname>` also compares what programs and systemd-resolved get for that host.

## Modes

Set exactly one in `.claude/openvpn3-on-demand.local.md` (both or neither is an error; the skill then skips the VPN):

- **BYO** — `profile_name` names a profile you imported with `openvpn3 config-import --persistent`. Before each start the plugin writes the `dns-scope=tunnel` baseline and any `config_overrides` into that profile, so they also apply to your own `openvpn3 session-start`. Removing a key from `config_overrides` doesn't remove it from the profile: run `openvpn3 config-manage --config <profile_name> --unset-override <key>` (`openvpn3 config-manage --config <profile_name> --show` lists what's set).
- **Ephemeral** — `ovpn_provision_cmd` prints the `.ovpn` body on **stdout**. Each VPN-gated turn the plugin runs it, writes the output to a mode-600 file in a private per-user directory, imports it single-use as `ovpn3-od-$CLAUDE_CODE_SESSION_ID`, connects, and deletes the file.

Commented templates: [`skills/vpn-on-demand/references/example-local-settings.md`](skills/vpn-on-demand/references/example-local-settings.md).

## When does the tunnel come up?

**Claude decides per operation.** Nothing in the plugin watches traffic or knows on its own what sits behind your VPN. Claude goes by:

- **Built-in hints** — private AWS endpoints (`*.rds.amazonaws.com`, `*.elasticache.amazonaws.com`, …), hostnames ending in `.internal`, `.corp`, `.private` or `.vpc`, and private IPs used with `ssh`, `kubectl`, `mysql`, `psql`, `curl` and similar.
- **Your project's `CLAUDE.md` / README** — anything described as internal or VPN-only.

Many operations don't show their target in the command: a DB client reading its host from a config file or login path (like the `mysql-client` plugin), a kubeconfig context, an SSH alias, a `make` target or script, a secret-store CLI fetching from an internal Vault, any tool or plugin that reads its endpoint from its own settings. Tell Claude about those:

- **In the project's `CLAUDE.md`** (best — read every session):
  ```markdown
  The staging database, vault.corp.example and the Kubernetes API are only reachable over the VPN.
  ```
- **Or just ask** — "connect the VPN first".

A timeout or "could not resolve host" on something private usually means the tunnel wasn't up; the skill suspects that first. If it was up, see [Troubleshooting](#tunnel-up-but-private-names-dont-resolve).

## MCP tools

| Tool                    | Args                          | Returns                                                  |
|-------------------------|-------------------------------|----------------------------------------------------------|
| `vpn_status`            | —                             | `{status: ok, session_count, sessions: [...]}`           |
| `vpn_connect`           | `profile_name`, `overrides?`  | `{status: connected \| already_connected \| error, ...}` |
| `vpn_connect_ephemeral` | `ovpn_path`, `session_id`, `overrides?` | `{status: connected \| already_connected \| error, ...}` |
| `vpn_disconnect`        | `profile_name`                | `{status: disconnected \| not_connected \| error, ...}`  |

`vpn_connect` is for imported BYO profiles; `vpn_connect_ephemeral` imports a fresh `.ovpn` single-use. The skill passes `$CLAUDE_CODE_SESSION_ID` as `session_id` because the MCP server (a singleton across `/resume`/`/fork-session`) can't see it. Both apply a `dns-scope=tunnel` baseline — split DNS: only the VPN's pushed domains go to its DNS, so inside systemd-resolved it coexists with Tailscale, mDNS and other VPNs. `overrides` override it; values the profile already holds aren't rewritten.

`already_connected` means the session is up; one still connecting is waited for. Any other state (paused, failed, dead backend) returns an `error` naming it and the command that clears it (`openvpn3 session-manage --disconnect --config <name>`, or `openvpn3 session-manage --cleanup` for a dead backend). The server never tears down a session it didn't start in that call.

## Security

- Ephemeral mode writes the `.ovpn` to a mode-600 file at a fixed per-session path — `$XDG_RUNTIME_DIR/openvpn3-on-demand/` (memory-backed, wiped at logout), or `~/.cache/openvpn3-on-demand/` when `$XDG_RUNTIME_DIR` isn't set — hands it to openvpn3 over D-Bus and deletes it. Its contents never enter the transcript. An interrupted turn leaves at most that one file, which the next turn overwrites; `rm -rf` the directory to clear it.
- `.claude/openvpn3-on-demand.local.md` may hold internal hostnames and commands; `/openvpn3-on-demand:setup` adds it to `.gitignore`.
- The MCP server exits 1 if `dbus` or `openvpn3` can't be imported. It installs nothing and never runs as root.
- The skill only disconnects the profile in the settings file. After a crash the tunnel stays up: `openvpn3 session-manage --disconnect --config <profile_name>` (BYO) or `… --config "ovpn3-od-$CLAUDE_CODE_SESSION_ID"` (ephemeral).

## Troubleshooting

- **Anything looks broken** — `/openvpn3-on-demand:doctor` tests every prerequisite and prints the fix.
- **`cannot import 'dbus' and/or 'openvpn3'`** — install `openvpn3-client` and `python3-dbus`, restart Claude Code.
- **`vpn_connect` says the config is unknown** — BYO: `openvpn3 config-import --name <profile_name> --persistent --config /path/to/file.ovpn`.
- **Tunnel up, but private names fail, resolve to `0.0.0.0`, or connections hang** — [below](#tunnel-up-but-private-names-dont-resolve).

Settings-file edits apply immediately, except a key removed from `config_overrides` in BYO mode (see [Modes](#modes)). Only changes to the plugin's `.mcp.json` need a restart.

### Tunnel up, but private names don't resolve

`resolvectl query <host>` returns the private address, yet programs get nothing or `0.0.0.0`, or hang. Connecting to `0.0.0.0` reaches *this* machine, so a TLS client (`redis-cli --tls`, `psql` with `sslmode=require`) can hang against a local service with no DNS error.

Two layers have to work:

1. **The tunnel's DNS reaches systemd-resolved** — the netcfg init in [Prerequisites](#prerequisites). `resolvectl dns` lists the VPN's servers on the tunnel link, `resolvectl domain` its pushed domains. Under `dns-scope=tunnel` only those domains go to the tunnel; a VPN that pushes none needs `dhcp-option DOMAIN <domain>` in its `.ovpn`.
2. **Programs ask systemd-resolved.** glibc programs (redis-cli, psql, mysql, curl, Python, Node's `dns.lookup`, Java) follow `hosts:` in `/etc/nsswitch.conf`: `resolve` goes to systemd-resolved; `dns` goes to `/etc/resolv.conf`, which must be the `127.0.0.53` stub. If Tailscale, a VPN client or NetworkManager owns that file, programs ask a resolver that knows nothing of the split DNS. Public DNS returns private AWS names' private IPs, but resolvers with DNS-rebinding protection (NextDNS, many routers) answer `0.0.0.0`; private zones (`*.compute.internal`, Route 53 private zones) don't resolve at all.

Check with the tunnel up: `/openvpn3-on-demand:doctor <private-hostname>`, or compare `resolvectl query --synthesize=no <host>` (systemd-resolved, ignoring `/etc/hosts`) with `getent ahosts <host>` (what programs get). The skill runs this check for each private host after connecting and stops before the command if the check fails (programs get `0.0.0.0`, nothing, or a different address for a name under the tunnel's domains).

**Fixes** — all need root; the plugin prints them, never runs them.

- **Point `/etc/resolv.conf` at the stub** (recommended; fixes every program on this host):
  ```bash
  sudo ln -sfn ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
  ```
  If Tailscale had the file, also `sudo rm -f /etc/resolv.pre-tailscale-backup.conf` and `sudo systemctl restart tailscaled`; it then configures its DNS inside systemd-resolved, MagicDNS keeps working, and its catch-all `~.` loses to the tunnel's more specific domains. If NetworkManager writes the file, first set `dns=systemd-resolved` under `[main]` in a file in `/etc/NetworkManager/conf.d/` and restart it.
- **Install nss-resolve** — `sudo apt install libnss-resolve` adds `resolve [!UNAVAIL=return]` before `dns` in `/etc/nsswitch.conf` (keep `mdns4_minimal [NOTFOUND=return]` ahead of it). Covers glibc programs only; `dig`, `host`, `nslookup`, cgo-less Go and Node's `dns.resolve*` still read `/etc/resolv.conf`. A good fallback alongside the stub fix.
- **Containers get neither fix** — Docker's default network copies the host's resolver list (the upstream servers when the host uses the stub) without per-link routing; run VPN-bound clients on the host.
- **Not recommended:** disabling rebinding protection or allowlisting AWS names at your DNS provider (leaks internal names, misses private zones); `tailscale set --accept-dns=false` (drops MagicDNS, useless while Tailscale is stuck); a tailnet split-DNS route for `amazonaws.com` (affects every device, breaks AWS lookups when the tunnel is down).

**Tailscale can get stuck.** If tailscaled writes `/etc/resolv.conf` itself ("direct" mode — e.g. it restarted while a VPN client such as Cisco Secure Client had replaced the stub) and then stops uncleanly, it misreads its own leftover file on the next start and fails every DNS update: `tailscale status` shows `running /usr/sbin/resolvconf -m 0 -x -a tailscale: Failed to resolve interface "tailscale"`. Restarts and reboots don't clear it ([tailscale/tailscale#19062](https://github.com/tailscale/tailscale/issues/19062)); the stub fix does.

`/etc/hosts` pins and resolver patches cover one name each and go stale when the endpoint moves; the doctor flags a pin for the host you give it. Remove them after the fix.

## License

AGPL-3.0-only — the plugin links the AGPL `openvpn3` Python module. See [LICENSE](../../LICENSE).
