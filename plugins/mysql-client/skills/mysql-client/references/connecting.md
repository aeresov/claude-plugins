# Connecting

How to authenticate to MySQL/MariaDB without ever putting a password on the command line.

## The four credential sources `mysql` reads

`mysql` looks for credentials in this order. The first one that matches wins for any given option.

1. **`--login-path=<name>`** — encrypted file at `~/.mylogin.cnf`, managed by `mysql_config_editor`. **MySQL only**; MariaDB packages don't ship `mysql_config_editor`.
2. **`--defaults-file=<path>`** / **`--defaults-extra-file=<path>`** — explicit option file. `--defaults-file` *replaces* the default search path; `--defaults-extra-file` *adds* to it.
3. **`~/.my.cnf`** — per-user default. Sections: `[client]` (read by every client), `[mysql]` (the CLI only), `[mysqldump]`, etc.
4. **`/etc/mysql/my.cnf` and friends** — system-wide. Usually empty of credentials.

`-p<password>` on the command line is **never** acceptable. It's visible to anyone who can `ps`, gets logged by audit systems, and ends up in shell history. Treat anyone suggesting it as wrong.

## Login-paths (MySQL only)

One-time setup per host/user/role you need to connect as:

```bash
mysql_config_editor set \
  --login-path=prod-reader \
  --host=db.internal --user=reader --port=3306 --password
# Prompts for the password; stores in ~/.mylogin.cnf (mode 600, weakly encrypted).
```

Then everywhere:

```bash
mysql --login-path=prod-reader
mysql --login-path=prod-reader -e 'SELECT 1'
mysqldump --login-path=prod-reader ...        # other client tools too
```

List / remove:

```bash
mysql_config_editor print --all
mysql_config_editor remove --login-path=prod-reader
```

Caveat: `~/.mylogin.cnf` is obfuscated, not encrypted with a user-supplied key. Anyone with read access to the file (root, the user's own processes) can decrypt it. Treat it as "not in clear text", not as "secret-store-grade".

## `~/.my.cnf` (MySQL and MariaDB)

Plain INI, must be `chmod 600`:

```ini
[client]
host=db.internal
user=reader
password=...
default-character-set=utf8mb4

[mysql]
prompt="\u@\h [\d]> "
pager=less -SFXR
auto-rehash
```

The `[client]` section is read by `mysql`, `mysqldump`, `mysqladmin`, etc. The `[mysql]` section is the CLI-specific UX (prompt, pager, completion). Splitting by section keeps passwords out of pre-prompt code.

For multiple hosts, use a single `~/.my.cnf` with sections and pass `--defaults-group-suffix`:

```ini
[clientprod]
host=db.prod.internal
user=reader
password=...

[clientstage]
host=db.stage.internal
user=reader
password=...
```

```bash
mysql --defaults-group-suffix=prod        # reads [clientprod]
mysql --defaults-group-suffix=stage       # reads [clientstage]
```

## `--defaults-file` (scripted / per-task)

For ephemeral configs (e.g. a credentials file produced by a secret manager):

```bash
umask 077
secret=$(mktemp --suffix=.cnf)
cat > "$secret" <<EOF
[client]
host=db.internal
user=reader
password=$(vault read -field=password secret/db/reader)
EOF
mysql --defaults-file="$secret" -e 'SELECT 1'
rm -f "$secret"
```

`--defaults-file` *replaces* the search path — your `~/.my.cnf` is ignored. Use `--defaults-extra-file` if you want it to layer on top instead.

## Sockets vs TCP

Local connections default to a Unix socket if no `--host` (or `--host=localhost`) is given. Common socket paths: `/var/run/mysqld/mysqld.sock` (Debian/Ubuntu), `/var/lib/mysql/mysql.sock` (RHEL family), `/tmp/mysql.sock` (Homebrew).

```bash
mysql -S /var/run/mysqld/mysqld.sock -u reader
mysql --host=127.0.0.1 --port=3306 -u reader     # forces TCP even when "localhost"
```

`--host=localhost` selects the socket; `--host=127.0.0.1` forces TCP. This trips people up — if you need to talk to a port on the same host (e.g. a tunneled forwarder), say `127.0.0.1`.

## SSL / TLS

`mysql --ssl-mode=...`:

| Mode               | Server cert checked? | Hostname verified? | Use when                                |
|--------------------|----------------------|---------------------|------------------------------------------|
| `DISABLED`         | —                    | —                   | dev only; refuses TLS                    |
| `PREFERRED`        | no                   | no                  | default; falls back to plaintext silently |
| `REQUIRED`         | no                   | no                  | minimum bar for prod                     |
| `VERIFY_CA`        | yes (via `--ssl-ca`) | no                  | pinned CA, host can differ                |
| `VERIFY_IDENTITY`  | yes                  | yes                 | use for prod across networks              |

```bash
mysql --login-path=prod-reader \
  --ssl-mode=VERIFY_IDENTITY \
  --ssl-ca=/etc/ssl/certs/rds-combined-ca-bundle.pem
```

`--ssl-mode=PREFERRED` is the default and is **not** safe across an untrusted network — it negotiates TLS but doesn't verify anything. For RDS/Aurora over the public internet (or even cross-VPC), use at least `VERIFY_CA` with the AWS-published CA bundle.

## Verify the connection

Run this first, before any other work. Surface the answer:

```sql
SELECT
  @@hostname,
  @@version,
  @@version_comment,        -- "MySQL Community Server" / "MariaDB" / "Percona Server"
  @@read_only,
  @@super_read_only,
  USER(),                   -- "reader@10.0.1.42" — the connection the server sees
  CURRENT_USER(),           -- "reader@%" — the granted identity
  DATABASE(),               -- NULL if no default set
  @@global.transaction_isolation;
```

Cross-check `USER()` vs `CURRENT_USER()`: if they differ on the host part, the server matched a wildcard `%` grant, which often means the principal-of-least-privilege design has gaps.

Then check what you can actually do:

```sql
SHOW GRANTS;                  -- you, current host
SHOW GRANTS FOR CURRENT_USER; -- same
```

If you don't have `SELECT` on the schemas you need, **stop and tell the user** — don't try to work around the grant.

## Provider quirks

- **Amazon RDS / Aurora MySQL.** SSL is on by default; use the AWS CA bundle (`rds-combined-ca-bundle.pem`) with `--ssl-mode=VERIFY_IDENTITY`. The endpoint is `<id>.<region>.rds.amazonaws.com`. Cluster endpoints write; reader endpoints are forced read-only — verify with `@@read_only`.
- **Google Cloud SQL.** Same SSL story; CA at `server-ca.pem`. Cloud SQL Proxy connects via a Unix socket — pair with `-S` not TCP.
- **PlanetScale.** Uses MySQL wire protocol but enforces SSL and rejects some DDL. Always use the proxy or the published TLS cert.
- **MariaDB.** Mostly drop-in for client purposes. `mysql_config_editor` isn't available — fall back to `~/.my.cnf`. New MariaDB packages ship the binary as `mariadb` (with a `mysql` symlink). `@@version_comment` is `"MariaDB Server"`.

## Common failure modes

- **`Access denied for user 'reader'@'10.0.1.42'`** — wrong host part of the grant. Run `SHOW GRANTS;` (as the user that *does* work) for `'reader'@'%'` etc.
- **`SSL connection error: SSL_CTX_set_default_verify_paths failed`** — `--ssl-ca` path is wrong or the CA bundle isn't readable.
- **`Can't connect to local MySQL server through socket`** — `--host=localhost` picked a socket that doesn't exist. Try `--host=127.0.0.1` or `-S <correct path>`.
- **`ERROR 2026 (HY000): TLS/SSL error: Server certificate verification failed`** with `VERIFY_IDENTITY` — hostname doesn't match cert. Use `VERIFY_CA` if you're sure of the CA but not the hostname (e.g. behind a tunnel).
- **Auth plugin mismatch** (`Plugin '...' is not loaded`) — usually `mysql_native_password` vs `caching_sha2_password`. Older clients can't speak the newer plugin; upgrade `mysql-client` or have the user's account altered to `mysql_native_password` (server-side).
