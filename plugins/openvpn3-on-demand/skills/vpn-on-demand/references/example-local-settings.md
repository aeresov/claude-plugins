# Example `.claude/openvpn3-on-demand.local.md`

Drop this at `.claude/openvpn3-on-demand.local.md` in the project root, edit the
fields, and add `.claude/*.local.md` to `.gitignore`.

```markdown
---
# REQUIRED. Name of the openvpn3 config to start/stop. This is the name you
# passed to `openvpn3 config-import --name <name>` (or will pass via
# ovpn_provision_cmd + vpn_config_import below).
profile_name: my-prod-vpn

# OPTIONAL. Shell command that produces a fresh .ovpn file on first connect.
# The skill runs this iff vpn_connect fails because the profile isn't imported
# yet. Point it at a deterministic output path you also reference when calling
# vpn_config_import. Example: a Makefile target that fetches the file from a secrets
# manager or generates it on the fly.
ovpn_provision_cmd: make get_vpn_client_config OUTPUT=~/.config/openvpn3/my-prod-vpn.ovpn

# OPTIONAL. Extra regex patterns to treat as VPN-requiring, in addition to the
# defaults listed in the vpn-on-demand skill (RDS/ElastiCache/MemoryDB hosts,
# RFC1918 addresses, .internal / .corp / .local / .private / .vpc hostnames,
# aws CLI against private services, kubectl against private clusters, etc).
#
# Each entry is matched against the full command string or target URL/host.
trigger_patterns:
  - "mysql .* -h [^ ]*\\.internal"
  - "kubectl --context prod-.*"
  - "aws (rds|elasticache|memorydb|secretsmanager|ssm) "

# OPTIONAL. Shell command run by the skill after a fresh `vpn_connect`
# succeeds (i.e. `status: connected`, not `already_connected`, so it does
# not repeat on every turn of a long conversation). Typical uses:
# - Warming a DNS cache / probing the VPC resolver:
#     dig +short internal-db.my-vpc.internal
# - Opening an ssh control master so later ssh commands multiplex:
#     ssh -fNMT my-bastion
# - Logging the event for auditability.
# A non-zero exit is surfaced to the user but does NOT tear down the
# tunnel — treat this as supplementary.
post_connect_cmd: dig +short internal-db.my-vpc.internal

# OPTIONAL. Shell command run by the skill after a fresh `vpn_disconnect`
# succeeds (not on `not_connected`). Typical uses:
# - Tearing down something `post_connect_cmd` set up:
#     ssh -O exit my-bastion
# - Flushing a DNS resolver cache:
#     sudo resolvectl flush-caches
# NOTE: the Stop / SessionEnd teardown hook (the safety net that
# disconnects if the model forgot to) does NOT run this — it's
# deliberately minimal. So if you rely on the hook rather than an
# explicit `vpn_disconnect`, your post-disconnect cleanup is skipped.
post_disconnect_cmd: echo "vpn disconnected at $(date -Is)"
---

# Project-specific VPN notes

Document anything non-obvious about this project's VPN setup here — which
account the profile corresponds to, how to rotate credentials, who to ping
when it's broken. This body is not consumed by the plugin; it's for humans
(and for Claude when reading CLAUDE.md-style context).
```

## How the fields flow through the plugin

- `profile_name` is passed verbatim as the argument to `vpn_connect`, `vpn_disconnect`, and `vpn_config_import`. Keep it in kebab-case for consistency with openvpn3's own conventions.
- `ovpn_provision_cmd` is invoked only when `vpn_connect` fails because no config with that name is registered. On success, the skill calls `vpn_config_import` and retries the connect. If this field is absent, the user must import the profile manually before the plugin is useful for them.
- `trigger_patterns` *extend* the skill's built-in defaults. Use it for project-specific internal hostnames or wrapper scripts.
- `post_connect_cmd` runs once per fresh connect, not on `already_connected`; intended for DNS warming, control masters, and logging. Non-fatal on failure.
- `post_disconnect_cmd` runs once per fresh disconnect, not on `not_connected`; pair it with `post_connect_cmd` for teardown. Skipped by the Stop/SessionEnd safety-net hook.

## Gitignore

The settings file contains environment-specific names and commands that might
point at internal infrastructure. Treat it as local state:

```gitignore
.claude/*.local.md
```
