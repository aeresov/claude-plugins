# Example `.claude/openvpn3-on-demand.local.md`

Save one template as `.claude/openvpn3-on-demand.local.md` in the project root and gitignore `.claude/*.local.md`; `/openvpn3-on-demand:setup` writes the same shape. Set **exactly one** of `profile_name` / `ovpn_provision_cmd`; both or neither is a configuration error.

## BYO mode

Use an openvpn3 config you imported yourself.

```markdown
---
profile_name: my-prod-vpn

# Optional fields — see "Fields" below.
post_connect_cmd: getent ahosts internal-db.my-vpc.internal
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

The plugin never creates or removes a BYO config, but writes the `dns-scope=tunnel` baseline and any `config_overrides` into it.

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
post_connect_cmd: getent ahosts internal-db.my-vpc.internal
post_disconnect_cmd: sudo resolvectl flush-caches
---


# Project VPN notes (for humans)

Where ovpn_provision_cmd pulls from, what credentials it needs, who owns the secret.
```

`ovpn_provision_cmd`'s **stdout** must be the `.ovpn` body — not a path or status line. The plugin writes it to a mode-600 file in a private per-user directory, imports it single-use and deletes the file; contents never enter the transcript.

Keep the settings file **task-agnostic**: don't hard-code per-task values (`ENV`, `AWS_PROFILE`, region, vault namespace, …); Claude adds them at call time from the project's `CLAUDE.md`. A Makefile provisioner stays as
```markdown
ovpn_provision_cmd: make infra-vpn-config OUTPUT=/dev/stdout
```
and Claude runs `AWS_PROFILE=<…> make infra-vpn-config OUTPUT=/dev/stdout ENV=<env>` each turn — `ENV` as a make argument, since Claude Code refuses a leading `ENV=` in worktree-isolated sessions.

## Fields

| Field | Mode | Required | Purpose |
|---|---|---|---|
| `profile_name` | BYO | one-of | Name of an openvpn3 config the user imported (`openvpn3 config-import --persistent`). |
| `ovpn_provision_cmd` | ephemeral | one-of | Shell command whose stdout is the `.ovpn` body. Re-run every VPN-gated turn. |
| `post_connect_cmd` | both | no | Run after a fresh `vpn_connect` (not on `already_connected`). Non-fatal. The skill's DNS check runs anyway; for a probe here use `getent` (resolves like programs do), not `dig` (asks `/etc/resolv.conf`'s server directly). |
| `post_disconnect_cmd` | both | no | Run after a fresh `vpn_disconnect` (not on `not_connected`). Non-fatal. |
| `config_overrides` | both | no | `{name: value}` openvpn3 `config-manage` overrides set before each tunnel start; values keep their YAML type. The server applies a `dns-scope=tunnel` (split-DNS) baseline; `dns-scope: global` overrides it, other entries like `log-level: 4` add to it. In BYO mode the baseline and these entries persist in the profile (also for a manual `openvpn3 session-start`); removing a key doesn't unset it — run `openvpn3 config-manage --config <profile_name> --unset-override <key>`. |

## Gitignore

The file may hold internal hostnames and provisioning commands:

```gitignore
.claude/*.local.md
```
