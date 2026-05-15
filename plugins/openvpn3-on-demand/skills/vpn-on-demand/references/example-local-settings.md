# Example `.claude/openvpn3-on-demand.local.md`

Drop this at `.claude/openvpn3-on-demand.local.md` in the project root, fill in
**one** of the two modes below, and add `.claude/*.local.md` to `.gitignore`.

## BYO mode — use an openvpn3 config you've already imported

```markdown
---
# REQUIRED in this mode. Name of an openvpn3 config you imported yourself, e.g.
#   openvpn3 config-import --config /path/to/my-prod-vpn.ovpn \
#                          --name my-prod-vpn --persistent
# The plugin only starts/stops sessions for it — it never creates or removes
# this config.
profile_name: my-prod-vpn

# OPTIONAL (both modes). Extra regex patterns to treat as VPN-requiring, on top
# of the vpn-on-demand skill's built-in defaults. Each entry is matched against
# the full command string or the target URL/host.
trigger_patterns:
  - "mysql .* -h [^ ]*\\.internal"
  - "kubectl --context prod-.*"

# OPTIONAL (both modes). Shell command run after a fresh vpn_connect
# (status: connected, not already_connected). Typical uses: warming a DNS cache,
# probing a VPC endpoint, opening an ssh control master, audit logging. A
# non-zero exit is surfaced but does NOT tear down the tunnel.
post_connect_cmd: dig +short internal-db.my-vpc.internal

# OPTIONAL (both modes). Shell command run after a fresh vpn_disconnect
# (status: disconnected, not not_connected). Typical uses: flushing a DNS
# resolver cache, closing port-forwards opened by post_connect_cmd. Also run by
# the Stop/SessionEnd safety-net hook when it disconnects (5s timeout, failures
# swallowed) — keep it quick and idempotent.
post_disconnect_cmd: sudo resolvectl flush-caches

# OPTIONAL (both modes). openvpn3 config-manage overrides reapplied to the
# configuration before each tunnel start. Keys are the hyphenated openvpn3
# override names — what `openvpn3 config-manage --help` calls them. Values
# preserve YAML types: bool stays bool, int stays int, anything else is a
# string.
#
# The canonical case is dns-scope=tunnel for split-DNS coexistence: openvpn3
# stops claiming the catch-all DNS domain (`~.`) and only handles the search
# domains pushed by the .ovpn (`dhcp-option DOMAIN <name>`). Required when
# Tailscale or another VPN is on the same host and you want each resolver to
# own its own slice of the namespace.
#
# config_overrides:
#   dns-scope: tunnel
#   persist-tun: true
#   log-level: 4
---

# Project-specific VPN notes for humans

Which account this profile is for, how to rotate its credentials, who to ping
when it breaks. This body is not consumed by the plugin.
```

## Ephemeral mode — generate a throwaway profile, regenerated every turn

```markdown
---
# REQUIRED in this mode (mutually exclusive with profile_name). A shell command
# whose STANDARD OUTPUT is the contents of a .ovpn file. The plugin captures
# stdout into a private temp file, imports it as a single-use config under an
# internal name you never see or pick, uses it, and lets openvpn3 drop it once
# the tunnel starts. Re-run on every turn that needs the VPN.
#
# Examples (pick whatever fits how you store the profile):
#   ovpn_provision_cmd: vault read -field=config secret/vpn/my-prod
#   ovpn_provision_cmd: aws s3 cp s3://my-bucket/vpn/my-prod.ovpn -
#   ovpn_provision_cmd: cat ~/.config/openvpn3/my-prod-vpn.ovpn
#   ovpn_provision_cmd: make get_vpn_client_config OUTPUT=/dev/stdout
ovpn_provision_cmd: vault read -field=config secret/vpn/my-prod

# OPTIONAL fields below behave exactly as in BYO mode — see above for the full
# descriptions.
trigger_patterns:
  - "aws (rds|elasticache|memorydb|secretsmanager|ssm) "
post_connect_cmd: dig +short internal-db.my-vpc.internal
post_disconnect_cmd: sudo resolvectl flush-caches
config_overrides:
  dns-scope: tunnel
---

# Project-specific VPN notes for humans

Where ovpn_provision_cmd pulls the profile from, what credentials it needs, who
owns that secret. This body is not consumed by the plugin.
```

## Pick exactly one mode

- `profile_name` **xor** `ovpn_provision_cmd`. Setting both, or neither, is a configuration error — the skill tells the user and skips the VPN entirely.
- `profile_name` is for a config you manage yourself with `openvpn3 config-import --persistent`; the plugin never creates or deletes it.
- `ovpn_provision_cmd` is for a throwaway profile regenerated on every VPN-gated turn; its **stdout must be the `.ovpn` body** — not a file path, not a status line.

## Gitignore

The settings file holds environment-specific names and commands that may point at
internal infrastructure. Treat it as local state:

```gitignore
.claude/*.local.md
```
