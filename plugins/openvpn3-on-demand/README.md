# openvpn3-on-demand

A Claude Code and Codex plugin that brings an OpenVPN tunnel up and down on demand — no always-on VPN, no `Bash(openvpn3 *)` permission grants.
**Linux only.** Two backends behind the same tools:

- **openvpn3** — talks to `openvpn3-linux` over D-Bus via the `openvpn3` Python module from `openvpn3-client`. Used wherever it is installed.
- **openvpn2** — runs the distribution's OpenVPN 2.6 in a transient systemd unit. For hosts that have no openvpn3 package, such as Amazon Linux 2023. See [OpenVPN 2 backend](#openvpn-2-backend).

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install openvpn3-on-demand@aeresov-claude-plugins
/openvpn3-on-demand:setup     # writes .claude/openvpn3-on-demand.local.md + .gitignore
```

Then ask the assistant for something that hits a private resource; the skill connects first. See [When does the tunnel come up?](#when-does-the-tunnel-come-up). `/openvpn3-on-demand:doctor` is a read-only health check.

## Codex

Register this repository and install `openvpn3-on-demand`:

```bash
codex plugin marketplace add aeresov/claude-plugins
codex plugin add openvpn3-on-demand@aeresov-codex-plugins
```

Use `$openvpn3-on-demand:openvpn3-on-demand-setup` and `$openvpn3-on-demand:openvpn3-on-demand-doctor` in a Codex prompt. The main skill is `$openvpn3-on-demand:vpn-on-demand`. Both clients use the same skills, implementation and (where applicable) gitignored `.claude/` project settings. These workflows need local execution with the prerequisites below. No global Codex configuration is changed by cloning this repository.

## Prerequisites

Both backends need [`uv`](https://docs.astral.sh/uv/) on `PATH` (it runs the MCP server) and a `python3` (it runs the plugin's checks). Where `/usr/bin/python3` is older than 3.10, the launcher has uv fetch a Python for the server.

**openvpn2 backend** (no openvpn3 on the host):

- OpenVPN 2.6 — the distribution's `openvpn` package (Amazon Linux 2023: `sudo dnf install openvpn`).
- systemd with journald storing unit output (the default), and `sudo` that runs the plugin's helper without a password — see [OpenVPN 2 backend](#openvpn-2-backend). Nothing else: no DNS setup, no D-Bus.

**openvpn3 backend:**

- `openvpn3-client` — D-Bus services and the `openvpn3` Python module (Debian/Ubuntu: `apt install openvpn3-client`).
- `python3-dbus` — not pulled in by `openvpn3-client` (Debian/Ubuntu: `apt install python3-dbus`).
- `/usr/bin/python3` ≥ 3.10: the server's venv is built on it to see those two packages.
- One-time netcfg init, so the tunnel's DNS reaches systemd-resolved:
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```
- Programs resolving through systemd-resolved: `/etc/resolv.conf` is the stub (Ubuntu's default) or `libnss-resolve` is installed. Tailscale, VPN clients and NetworkManager can take that file over — see [Troubleshooting](#tunnel-up-but-private-names-dont-resolve).

`/openvpn3-on-demand:doctor` says which backend the host has and checks all of this except the Python version (on the OpenVPN 2 backend: the OpenVPN version, that `systemd-run` is there, and that sudo allows the helper; a host whose journald drops unit output only shows at the first connect, which then fails with a message saying so). With the tunnel up, `/openvpn3-on-demand:doctor <private-hostname>` also compares what programs and systemd-resolved get for that host.

## Modes

Set exactly one in `.claude/openvpn3-on-demand.local.md` (both or neither is an error; the skill then skips the VPN):

- **BYO** — `profile_name` names a profile you imported with `openvpn3 config-import --persistent` (openvpn2: the file `/etc/openvpn/client/<profile_name>.conf`; the rest of this paragraph is openvpn3 only). Before each start the plugin writes the `dns-scope=tunnel` baseline and any `config_overrides` into that profile, so they also apply to your own `openvpn3 session-start`. Removing a key from `config_overrides` doesn't remove it from the profile: run `openvpn3 config-manage --config <profile_name> --unset-override <key>` (`openvpn3 config-manage --config <profile_name> --show` lists what's set).
- **Ephemeral** — `ovpn_provision_cmd` prints the `.ovpn` body on **stdout**. Each VPN-gated turn the plugin runs it, writes the output to a mode-600 file in a private per-user directory, imports it single-use as `ovpn3-od-<task-id>`, connects, and deletes the file.

Commented templates: [`skills/vpn-on-demand/references/example-local-settings.md`](skills/vpn-on-demand/references/example-local-settings.md).

## When does the tunnel come up?

**The assistant decides per operation.** Nothing in the plugin watches traffic or knows on its own what sits behind your VPN. It goes by:

- **Built-in hints** — private AWS endpoints (`*.rds.amazonaws.com`, `*.elasticache.amazonaws.com`, …), hostnames ending in `.internal`, `.corp`, `.private` or `.vpc`, and private IPs used with `ssh`, `kubectl`, `mysql`, `psql`, `curl` and similar.
- **Your project's `AGENTS.md` / `CLAUDE.md` / README** — anything described as internal or VPN-only.

Many operations don't show their target in the command: a DB client reading its host from a config file or login path (like the `mysql-client` plugin), a kubeconfig context, an SSH alias, a `make` target or script, a secret-store CLI fetching from an internal Vault, any tool or plugin that reads its endpoint from its own settings. Describe those in the project instructions:

- **In the project's `AGENTS.md` (Codex) or `CLAUDE.md` (Claude)** (best — read every session):
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

`vpn_status` and the connect results also carry `backend` (`openvpn3` or `openvpn2`), and an openvpn2 `connected` result has `notes`: the routes the tunnel carries and what was not applied.

`vpn_connect` is for imported BYO profiles; `vpn_connect_ephemeral` imports a fresh `.ovpn` single-use. The skill passes a retained task identifier as `session_id`: `CLAUDE_CODE_SESSION_ID` in Claude or `CODEX_THREAD_ID` in Codex when available, otherwise a generated UUID. The MCP server cannot infer the calling chat's identity. On openvpn3 both apply a `dns-scope=tunnel` baseline — split DNS: only the VPN's pushed domains go to its DNS, so inside systemd-resolved it coexists with Tailscale, mDNS and other VPNs. `overrides` override it; values the profile already holds aren't rewritten.

`already_connected` means the session is up; one still connecting is waited for. Any other state (paused, failed, dead backend) returns an `error` naming it and the command that clears it (`openvpn3 session-manage --disconnect --config <name>`, or `openvpn3 session-manage --cleanup` for a dead backend; on openvpn2 `sudo systemctl stop openvpn3-on-demand-<name>.service`). The server never tears down a session it didn't start in that call.

## OpenVPN 2 backend

Used on hosts that have no openvpn3 at all and where `/usr/sbin/openvpn` exists. A host with openvpn3 installed stays on the openvpn3 backend — if its Python modules don't import there, the server says so and exits rather than quietly switching. `OPENVPN3_OD_BACKEND=openvpn2` (or `openvpn3`) in the server's environment forces one on a host that has both. Supported: OpenVPN 2.6.x. Tested on Amazon Linux 2023.

**How a tunnel runs.** Each tunnel is one transient systemd unit, `openvpn3-on-demand-<profile>.service`, running `/usr/sbin/openvpn` with only the capabilities the distribution's own client unit has. Once the tunnel is set up OpenVPN drops to the `openvpn` user where that account exists (the Fedora/RHEL/Amazon Linux package creates it). Debian's and Ubuntu's package creates none: there it keeps running as root and the connect result's `notes` says so; `sudo adduser --system --group openvpn` gives it an account to drop to. `systemctl status 'openvpn3-on-demand-*'` and `journalctl -u openvpn3-on-demand-<profile>` show it.

**Root.** OpenVPN 2 has no unprivileged service to ask for a tunnel, so starting and stopping one goes through `sudo -n` to one file, `servers/openvpn3/src/openvpn3_mcp/ovpn2_helper.py`. Reading state needs no privileges. Two ways to allow it:

- The user has passwordless sudo (the default for `ec2-user` on Amazon Linux): nothing to set up.
- Otherwise install the helper root-owned and allow only that:
  ```bash
  sudo install -D -o root -g root -m 0755 <plugin root>/servers/openvpn3/src/openvpn3_mcp/ovpn2_helper.py /usr/local/libexec/openvpn3-on-demand/ovpn2-helper
  echo "$USER ALL=(root) NOPASSWD: /usr/local/libexec/openvpn3-on-demand/ovpn2-helper" | sudo tee /etc/sudoers.d/openvpn3-on-demand
  sudo visudo -cf /etc/sudoers.d/openvpn3-on-demand
  ```
  Reinstall it after a plugin update: the server compares the installed copy with the one it ships and refuses to start a tunnel through one that differs (the error and the doctor's check 2 print the install line again). Stopping a tunnel still works with an older copy.

  The rule and the copy are independent of the plugin: they keep letting this user run that copy as root after the plugin is removed. To take the grant back, stop any tunnel and delete both:
  ```bash
  sudo systemctl stop 'openvpn3-on-demand-*'
  sudo rm /etc/sudoers.d/openvpn3-on-demand
  sudo rm -r /usr/local/libexec/openvpn3-on-demand
  ```

**What the helper won't do**, because OpenVPN runs as root there:

- *Profiles are an allowlist.* `up`, `plugin`, `script-security` and friends run code as root; `log`, `status`, `writepid`, `management` write files as root. A profile may hold only connection settings (`client`, `proto`, `remote`, `remote-random-hostname`, `nobind`, `remote-cert-tls server`, `verify-x509-name`, `cipher` / `data-ciphers` / `auth` / TLS settings, `reneg-sec`, keepalive and MTU settings) and inline `<ca>`, `<cert>`, `<key>`, `<tls-auth>`, `<tls-crypt>`, `<tls-crypt-v2>`, `<extra-certs>`, `<auth-user-pass>` blocks. Anything else refuses the profile and names the line. An AWS Client VPN export passes as it is. No script ever runs.
- *Only private routes, all on the tunnel device.* OpenVPN itself installs no routes (`--route-noexec`), and the only pushed options it accepts are the few a split tunnel needs; everything else is dropped however it is spelled, so no pushed `redirect-gateway`, DNS or IPv6 option takes effect. The helper reads the prefixes the server pushed from the unit's journal and adds those inside `10/8`, `172.16/12`, `192.168/16` or `100.64/10` itself, bound to the tunnel device at a high metric; the others — and any the server wants sent through another gateway — are left out and listed in the connect result. So every route disappears with the device, even after a hard kill. Addresses inside a network the host is directly attached to stay local: a pushed prefix inside one is left out, the route for the tunnel's own address is moved to the same high metric (so on an equal prefix the host's own route wins), and a VPN whose address pool would carve a piece out of the host's network is refused. A VPN that pushes a wider network around the host's own — a VPC's `/16` to a host in one of its `/24`s — doesn't cut it off. Where another route for the very same prefix is already there (a second VPN pushing it, a static route), that one keeps the traffic and the connect result says so. IPv6 is switched off on the tunnel device, so a router advertisement sent down the tunnel cannot hand the host an IPv6 route. Then the helper checks the address the VPN handed out and that the host's default gateway, DNS servers and `169.254.169.254` are reached exactly as before. If not, the tunnel is taken down again and the error says why. A timer armed before the connect stops the tunnel two minutes later should the helper die before that check.
- *No DNS changes.* OpenVPN 2.6 does nothing with pushed DNS without a script, and none is configured. Names resolve as they do without the tunnel. That is enough when the private names resolve to private addresses in public DNS (AWS RDS, ElastiCache and MemoryDB endpoints do) and the host's resolver doesn't filter them; a name only the private network's DNS knows won't resolve. `/openvpn3-on-demand:doctor <hostname>` tells which it is.
- *No lingering tunnels.* A tunnel never reconnects by itself: when the server stops answering it exits after the keepalive timeout the server pushes (20 s on AWS Client VPN; 120 s if it pushes none). It also exits after 60 min with next to no traffic (under 8 KB), and systemd stops it after 12 h. Connecting again is always safe. One address is tried per connect, and always the first: if it doesn't answer, the attempt fails after about 20 s, and a backup `remote` line is only ever reached when the profile has `remote-random`.

`config_overrides` are openvpn3 settings and are not applied here; the connect result says so. BYO profiles live at `/etc/openvpn/client/<profile_name>.conf` (root-owned, mode 600); the plugin never writes there. Not covered: profiles that prompt for credentials or whose private key is passphrase-protected (refused), IPv6 over the tunnel, full-tunnel VPNs, and SELinux in enforcing mode (untested; in permissive mode the unit logs denials for reading its profile under `/run` and for entering the `openvpn_t` domain).

## Security

- Ephemeral mode writes the `.ovpn` to a mode-600 file at the retained task-specific path — `$XDG_RUNTIME_DIR/openvpn3-on-demand/` (memory-backed, wiped at logout), or `~/.cache/openvpn3-on-demand/` when `$XDG_RUNTIME_DIR` isn't set — hands it to openvpn3 over D-Bus (openvpn2: to the helper on stdin, which keeps a vetted root-only copy under `/run/openvpn3-on-demand/` until the tunnel stops) and deletes it. Its contents never enter the transcript. An interrupted turn can leave that file behind. Remove only its retained path; another chat may still be using files in the same directory.
- `.claude/openvpn3-on-demand.local.md` may hold internal hostnames and commands; `/openvpn3-on-demand:setup` adds it to `.gitignore`.
- The MCP server exits 1 if no backend is usable, or if openvpn3 is installed but its Python modules don't import. It installs nothing and never runs as root; on the openvpn2 backend it calls one helper through `sudo -n` to start and stop tunnels ([OpenVPN 2 backend](#openvpn-2-backend)).
- The skill disconnects the configured BYO profile or its own ephemeral profile returned by the connect tool. After a crash the tunnel stays up: `openvpn3 session-manage --disconnect --config <profile_name>` (BYO) or `… --config "ovpn3-od-<task-id>"` (ephemeral). On openvpn2 it closes itself when idle; to stop it at once, `sudo systemctl stop openvpn3-on-demand-<profile_name>.service`.

## Troubleshooting

- **Anything looks broken** — `/openvpn3-on-demand:doctor` tests every prerequisite and prints the fix.
- **`no VPN backend found`** — install `openvpn3-client` and `python3-dbus`, or the distribution's `openvpn` 2.6 package; restart the client.
- **`openvpn3 is installed, but 'dbus' and/or 'openvpn3' can't be imported`** — an openvpn3 host whose Python modules don't load: install `python3-dbus` (`openvpn3-client` doesn't pull it in) and restart the client. Installing `openvpn` doesn't help there: on a host with openvpn3 the server only uses OpenVPN 2 when `OPENVPN3_OD_BACKEND=openvpn2` is set.
- **`vpn_connect` says the config is unknown** — BYO: `openvpn3 config-import --name <profile_name> --persistent --config /path/to/file.ovpn` (openvpn2: `sudo install -m 600 -o root -g root /path/to/file.ovpn /etc/openvpn/client/<profile_name>.conf`).
- **`OpenVPN 2 backend: … sudo -n … refused`** — allow the helper: [OpenVPN 2 backend](#openvpn-2-backend).
- **The server won't start after a system Python upgrade or downgrade** — the launcher rebuilds `servers/openvpn3/.venv` when it no longer fits the host; if that fails, delete the directory.
- **Tunnel up, but private names fail, resolve to `0.0.0.0`, or connections hang** — [below](#tunnel-up-but-private-names-dont-resolve).

Settings-file edits apply immediately, except a key removed from `config_overrides` in BYO mode (see [Modes](#modes)). Refresh/reinstall the plugin after package changes; restart the client if its MCP configuration or skills are stale.

### Tunnel up, but private names don't resolve

This section is about the openvpn3 backend, which hands the tunnel's DNS to systemd-resolved. The openvpn2 backend leaves DNS alone ([OpenVPN 2 backend](#openvpn-2-backend)); there, `/openvpn3-on-demand:doctor <private-hostname>` only says what programs get for the name, and none of the fixes below apply.

`resolvectl query <host>` returns the private address, yet programs get nothing or `0.0.0.0`, or hang. Connecting to `0.0.0.0` reaches *this* machine, so a TLS client (`redis-cli --tls`, `psql` with `sslmode=require`) can hang against a local service with no DNS error.

Two layers have to work:

1. **The tunnel's DNS reaches systemd-resolved** — the netcfg init in [Prerequisites](#prerequisites). `resolvectl dns` lists the VPN's servers on the tunnel link, `resolvectl domain` its pushed domains. Under `dns-scope=tunnel` only those domains go to the tunnel; a VPN that pushes none needs `dhcp-option DOMAIN <domain>` in its `.ovpn`.
2. **Programs ask systemd-resolved.** glibc programs (redis-cli, psql, mysql, curl, Python, Node's `dns.lookup`, Java) follow `hosts:` in `/etc/nsswitch.conf`: `resolve` goes to systemd-resolved; `dns` goes to `/etc/resolv.conf`, which must be the `127.0.0.53` stub. If Tailscale, a VPN client or NetworkManager owns that file, programs ask a resolver that knows nothing of the split DNS. Public DNS returns private AWS names' private IPs, but resolvers with DNS-rebinding protection (NextDNS, many routers) answer `0.0.0.0`; private zones (`*.compute.internal`, Route 53 private zones) don't resolve at all.

Check with the tunnel up: `/openvpn3-on-demand:doctor <private-hostname>`, or compare `resolvectl query --synthesize=no <host>` (systemd-resolved, ignoring `/etc/hosts`) with `getent ahosts <host>` (what programs get). The skill runs this check for each private host after connecting and stops before the command if the check fails (programs get `0.0.0.0`, nothing, or a different address for a name under the tunnel's domains).

**Fixes** — all need root; the plugin prints them, never runs them.

- **Point `/etc/resolv.conf` at the stub** (recommended; fixes every program on this host) — but **not** where systemd-resolved runs without its stub listener (`DNSStubListener=no`, the default on Amazon Linux): nothing answers on `127.0.0.53` there and the host would lose DNS. The doctor detects that and prints only the nss-resolve fix:
  ```bash
  sudo ln -sfn ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
  ```
  If Tailscale had the file, also `sudo rm -f /etc/resolv.pre-tailscale-backup.conf` and `sudo systemctl restart tailscaled`; it then configures its DNS inside systemd-resolved, MagicDNS keeps working, and its catch-all `~.` loses to the tunnel's more specific domains. If NetworkManager writes the file, first set `dns=systemd-resolved` under `[main]` in a file in `/etc/NetworkManager/conf.d/` and restart it.
- **Use nss-resolve** — `sudo apt install libnss-resolve` adds `resolve [!UNAVAIL=return]` before `dns` in `/etc/nsswitch.conf` (Fedora, RHEL and Amazon Linux ship the library with systemd; add the entry to the `hosts:` line yourself) (keep `mdns4_minimal [NOTFOUND=return]` ahead of it). Covers glibc programs only; `dig`, `host`, `nslookup`, cgo-less Go and Node's `dns.resolve*` still read `/etc/resolv.conf`. A good fallback alongside the stub fix.
- **Containers get neither fix** — Docker's default network copies the host's resolver list (the upstream servers when the host uses the stub) without per-link routing; run VPN-bound clients on the host.
- **Not recommended:** disabling rebinding protection or allowlisting AWS names at your DNS provider (leaks internal names, misses private zones); `tailscale set --accept-dns=false` (drops MagicDNS, useless while Tailscale is stuck); a tailnet split-DNS route for `amazonaws.com` (affects every device, breaks AWS lookups when the tunnel is down).

**Tailscale can get stuck.** If tailscaled writes `/etc/resolv.conf` itself ("direct" mode — e.g. it restarted while a VPN client such as Cisco Secure Client had replaced the stub) and then stops uncleanly, it misreads its own leftover file on the next start and fails every DNS update: `tailscale status` shows `running /usr/sbin/resolvconf -m 0 -x -a tailscale: Failed to resolve interface "tailscale"`. Restarts and reboots don't clear it ([tailscale/tailscale#19062](https://github.com/tailscale/tailscale/issues/19062)); the stub fix does.

`/etc/hosts` pins and resolver patches cover one name each and go stale when the endpoint moves; the doctor flags a pin for the host you give it. Remove them after the fix.

## License

AGPL-3.0-only — the openvpn3 backend links the AGPL `openvpn3` Python module. See [LICENSE](../../LICENSE).
