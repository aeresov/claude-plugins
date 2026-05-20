# Example `.claude/mysql-client.local.md`

Drop this template at `.claude/mysql-client.local.md` in the project root and add `.claude/*.local.md` to `.gitignore`.

The one frontmatter field — `connection_cmd` — is a shell command whose **stdout** is the body of a `[client]`-section INI. The plugin captures stdout into a mode-600 tempfile, runs `mysql --defaults-file=<tmp>` for every `mysql` call in the turn, and deletes the tempfile when the turn's mysql work is done.

The file's markdown body is human notes; only the frontmatter is read by the skill.

## Template

```markdown
---
# Stdout must be the body of a [client]-section INI; for example:
#
#   [client]
#   host=db.internal
#   port=3306
#   user=reader
#   password=...
#   ssl-mode=VERIFY_IDENTITY
#   ssl-ca=/etc/ssl/certs/rds-combined-ca-bundle.pem
#   default-character-set=utf8mb4
connection_cmd: vault read -field=mysql_client_ini secret/db/prod-reader
---

# Project DB notes (for humans — not read by the plugin)

What this points at, which secret store, who to ping when it breaks.
```

## Example commands

### Vault KV — single field holds the whole ini body

```yaml
connection_cmd: vault read -field=mysql_client_ini secret/db/prod-reader
```

The vault secret stores the full `[client]` ini in one field. Simplest to maintain when you control the secret-store schema.

### Vault KV — build the ini from discrete fields

```yaml
connection_cmd: |
  vault read -format=json secret/db/prod-reader | jq -r '
    "[client]",
    "host=\(.data.host)",
    "port=\(.data.port)",
    "user=\(.data.user)",
    "password=\(.data.password)",
    "ssl-mode=VERIFY_IDENTITY",
    "ssl-ca=/etc/ssl/certs/rds-combined-ca-bundle.pem"
  '
```

YAML's block scalar (`|`) handles multi-line commands. Use when the secret store schema isn't yours to change.

### AWS Secrets Manager — JSON secret

```yaml
connection_cmd: |
  aws secretsmanager get-secret-value --secret-id db/prod-reader \
       --query SecretString --output text \
  | jq -r '
    "[client]",
    "host=\(.host)",
    "port=\(.port)",
    "user=\(.username)",
    "password=\(.password)",
    "ssl-mode=VERIFY_IDENTITY",
    "ssl-ca=/etc/ssl/certs/rds-combined-ca-bundle.pem"
  '
```

The `aws secretsmanager` JSON convention varies by team; adjust the `jq` paths to match yours.

### AWS RDS IAM auth — rotating 15-minute token

```yaml
connection_cmd: |
  token=$(aws rds generate-db-auth-token \
    --hostname db.us-east-1.rds.amazonaws.com --port 3306 \
    --region us-east-1 --username iam-reader)
  cat <<EOF
  [client]
  host=db.us-east-1.rds.amazonaws.com
  port=3306
  user=iam-reader
  password=$token
  ssl-mode=VERIFY_IDENTITY
  ssl-ca=/etc/ssl/certs/rds-combined-ca-bundle.pem
  enable-cleartext-plugin
  EOF
```

`enable-cleartext-plugin` is required for RDS IAM auth — the token is sent as the password via the cleartext auth plugin over TLS. The token expires after 15 minutes; `connection_cmd` re-runs every turn, so each turn gets a fresh one.

### SOPS-encrypted file on disk

```yaml
connection_cmd: sops -d ~/.config/mysql/prod-reader.ini.sops
```

### 1Password CLI

```yaml
connection_cmd: op read "op://Engineering/db-prod-reader/ini"
```

### GnuPG-encrypted file on disk

```yaml
connection_cmd: gpg --quiet --decrypt ~/.config/mysql/prod-reader.ini.gpg
```

### Local file (last resort)

```yaml
connection_cmd: cat ~/.config/mysql/prod-reader.ini
```

Only viable if the file is `chmod 600` on an encrypted disk and you accept that anyone with shell access can read it. Prefer one of the secret-store options above.

## Field reference

| Field            | Required | Purpose                                                                                  |
|------------------|----------|------------------------------------------------------------------------------------------|
| `connection_cmd` | yes      | Shell command. Stdout = the `[client]`-section INI body. Re-run every turn that uses mysql. |

The plugin currently has only this one field. If the project's mysql work needs branching (dev vs staging vs prod), drive that off env vars in the command itself, not new frontmatter fields:

```yaml
connection_cmd: |
  vault read -field=mysql_client_ini "secret/db/${MYSQL_TARGET:-prod-reader}"
```

Then prepend `MYSQL_TARGET=staging` per-turn from the project's `CLAUDE.md` or the user's message.

## Gitignore

The settings file pulls a password into stdout each turn. Even though it's already mode-600 and ephemeral, the file itself names secret-store paths and shouldn't be committed:

```gitignore
.claude/*.local.md
```

## Security envelope

- **The stdout of `connection_cmd` never enters the conversation transcript.** The skill redirects it straight to a `mktemp` file with `umask 077`. Do not `cat` or otherwise echo the tempfile.
- **The tempfile is deleted at the end of the turn** (or the end of a dispatch to `mysql-investigator`). Don't leave it lying around.
- **`connection_cmd` runs in your shell with your env.** The plugin does not sanitise or sandbox the command — write it as carefully as you'd write any other shell that handles credentials.
- **Stderr from `connection_cmd` may be shown** for debugging. Don't print the password to stderr in your custom command.
- **No `-p<password>` in `connection_cmd` output.** The whole point is to keep passwords out of `ps`-visible command lines. The `[client]` ini's `password=...` line is fine because it's in a file read by `--defaults-file`, not a process argument.
