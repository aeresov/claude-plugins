# Example `.claude/openvpn3-on-demand.local.md`

Drop one of the templates below at `.claude/openvpn3-on-demand.local.md` in the project root and add `.claude/*.local.md` to `.gitignore`. `/openvpn3-on-demand:setup` writes the same shape interactively.

Set **exactly one** of `profile_name` / `ovpn_provision_cmd`. Both, or neither, is a configuration error.

## BYO mode

Use an openvpn3 config you imported yourself.

```markdown
---
profile_name: my-prod-vpn

# Optional fields — see "Fields" below.
trigger_patterns:
  - "mysql .* -h [^ ]*\\.internal"
  - "kubectl --context prod-.*"
post_connect_cmd: dig +short internal-db.my-vpc.internal
post_disconnect_cmd: sudo resolvectl flush-caches
config_overrides:
  log-level: 4
---

# Project VPN notes (for humans — not read by the plugin)

Which account this profile is for, how to rotate its credentials, who to ping when it breaks.
```

Import the profile once:

```bash
openvpn3 config-import --config /path/to/my-prod-vpn.ovpn \
                       --name my-prod-vpn --persistent
```

The plugin never creates or removes a BYO config.

## Ephemeral mode

Regenerate a throwaway profile every VPN-gated turn.

```markdown
---
# Stdout must be the .ovpn body. Pick whatever fits how you store the profile:
#   vault read -field=config secret/vpn/my-prod
#   aws s3 cp s3://my-bucket/vpn/my-prod.ovpn -
#   cat ~/.config/openvpn3/my-prod-vpn.ovpn
#   make get_vpn_client_config OUTPUT=/dev/stdout
ovpn_provision_cmd: vault read -field=config secret/vpn/my-prod

# Same optional fields as BYO mode.
trigger_patterns:
  - "aws (rds|elasticache|memorydb|secretsmanager|ssm) "
post_connect_cmd: dig +short internal-db.my-vpc.internal
post_disconnect_cmd: sudo resolvectl flush-caches
---


# Project VPN notes (for humans)

Where ovpn_provision_cmd pulls from, what credentials it needs, who owns the secret.
```

`ovpn_provision_cmd`'s **stdout** must be the `.ovpn` body — not a file path, not a status line. The plugin pipes stdout into a mode-600 temp file, imports it single-use, and deletes the file. Contents never enter the conversation transcript.

Keep settings files **task-agnostic**: per-task env vars (`ENV`, `AWS_PROFILE`, region, vault namespace, …) are supplied by Claude at call time from the project's `CLAUDE.md`, not hard-coded here. A Makefile-based provisioner stays as
```markdown
ovpn_provision_cmd: make infra-vpn-config OUTPUT=/dev/stdout
```
even though `make infra-vpn-config` needs `ENV=<env>` and `AWS_PROFILE=<…>` — the agent prepends those each turn.

## Fields

| Field | Mode | Required | Purpose |
|---|---|---|---|
| `profile_name` | BYO | one-of | Name of an openvpn3 config the user imported (`openvpn3 config-import --persistent`). |
| `ovpn_provision_cmd` | ephemeral | one-of | Shell command whose stdout is the `.ovpn` body. Re-run every VPN-gated turn. |
| `trigger_patterns` | both | no | Extra regex patterns treated as VPN-requiring, on top of the skill's built-in defaults. |
| `post_connect_cmd` | both | no | Shell command run after a fresh `vpn_connect` (not on `already_connected`). Non-fatal. |
| `post_disconnect_cmd` | both | no | Shell command run after a fresh `vpn_disconnect` (not on `not_connected`). Failures are non-fatal. |
| `config_overrides` | both | no | `{name: value}` map of openvpn3 `config-manage` overrides reapplied before each tunnel start. Values keep their YAML type. The server applies `dns-scope=tunnel` as a baseline (split-DNS); set `dns-scope: global` to override, or add other overrides like `log-level: 4`. |

## Gitignore

The settings file may contain internal hostnames and provisioning commands — keep it out of git:

```gitignore
.claude/*.local.md
```
