# mysql-client

A Claude Code plugin that makes Claude an effective, safe user of the Linux `mysql` (mysql-client) CLI against MySQL or MariaDB servers. Read-only-first by design — it teaches investigation, not modification.

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install mysql-client@aeresov-claude-plugins
/mysql-client:setup     # optional — configure connection discovery (see below)
```

Then ask Claude to do something against a MySQL/MariaDB database. As long as the request fits "connect, look, summarise" (rather than "alter, migrate, drop"), the skill picks it up.

Run `/mysql-client:doctor` any time for a read-only health check of the setup.

## Prerequisites

- The `mysql` CLI binary on `PATH`. Debian/Ubuntu: `apt install mysql-client` (or `default-mysql-client` / `mariadb-client`).
- A way to authenticate that doesn't put the password on the command line. Pick one:
  - **MySQL** — a login-path: `mysql_config_editor set --login-path=<name> --host=<h> --user=<u> --password` (prompts).
  - **MySQL or MariaDB** — a `~/.my.cnf` with a `[client]` section (chmod 600).
  - **Any** — a `--defaults-file=<path>` your script produces from a secret store.
- For RDS / Aurora / any TLS-required server, the appropriate CA bundle for `--ssl-mode=VERIFY_IDENTITY`.

See [`skills/mysql-client/references/connecting.md`](skills/mysql-client/references/connecting.md) for the full credential-source guide.

## Connection discovery (optional)

`/mysql-client:setup` configures this interactively — it picks the secret-store flavor, assembles the command, writes `.claude/mysql-client.local.md`, and updates `.gitignore`. Or drop the file in by hand: a `connection_cmd` frontmatter field, and the skill fetches credentials per turn — vault, AWS Secrets Manager, RDS IAM auth tokens, SOPS, 1Password, etc. The command's stdout must be a `[client]`-section INI body; the skill writes it to a mode-600 tempfile, runs `mysql --defaults-file=<tmp>`, deletes after the turn. Stdout never enters the conversation transcript.

Minimum file:

~~~markdown
---
connection_cmd: vault read -field=mysql_client_ini secret/db/prod-reader
---
~~~

Add `.claude/*.local.md` to `.gitignore`.

Field reference and a catalogue of `connection_cmd` examples for each secret-store flavour: [`skills/mysql-client/references/local-settings.md`](skills/mysql-client/references/local-settings.md).

## What triggers the skill

The skill loads when you ask Claude to:

- Connect to a MySQL/MariaDB database (e.g. "use the `prod-reader` login-path and …").
- Inspect schema (`SHOW`, `INFORMATION_SCHEMA`, "what tables …", "how do X and Y relate").
- Run a `SELECT` or read its output.
- Read `EXPLAIN` / `EXPLAIN ANALYZE` output, or diagnose a slow query.
- Look at `SHOW PROCESSLIST`, the slow log, `performance_schema`, replication state.

It does **not** fire on requests for migrations, schema changes, DML, server admin, or non-MySQL databases.

## What's in scope vs out

| Scope                                                | In / Out         |
|------------------------------------------------------|------------------|
| Read-only schema inspection                          | **In**           |
| Safe `SELECT`s, `EXPLAIN`, slow-query diagnosis      | **In**           |
| `--safe-updates`, login-paths, output formats        | **In**           |
| Replication / performance_schema / processlist reads | **In**           |
| `CREATE` / `ALTER` / `DROP` / `TRUNCATE`             | **Out**          |
| `INSERT` / `UPDATE` / `DELETE` / `REPLACE`           | **Out**          |
| `mysqldump`, `mysqladmin`, users/grants, backups     | **Out**          |
| Driver/library code (Python, Node, Go, …)            | **Out**          |
| Postgres, SQLite, Redis, anything-not-MySQL          | **Out**          |

If the request crosses an "out" line, the skill names what it can't do and hands it back to you with the proposed statement spelled out — never quietly runs it.

## The `mysql-investigator` subagent

For investigations that need many `mysql` calls — schema mapping across tables, slow-query root-causing, replication-lag triage, performance_schema digest analysis — the skill dispatches a read-only subagent that does the work in an isolated context and returns a focused writeup.

The subagent's tool allowlist (`Bash`, `Read`, `Grep`, `Glob`) excludes everything write-flavoured at the harness level, so even a buggy or misled investigator can't mutate the database or your filesystem.

See [`agents/mysql-investigator.md`](agents/mysql-investigator.md).

## Reference index

Loaded on demand by Claude as each step requires:

- [`connecting.md`](skills/mysql-client/references/connecting.md)
- [`local-settings.md`](skills/mysql-client/references/local-settings.md)
- [`safety-perimeter.md`](skills/mysql-client/references/safety-perimeter.md)
- [`output-formats.md`](skills/mysql-client/references/output-formats.md)
- [`schema-introspection.md`](skills/mysql-client/references/schema-introspection.md)
- [`explain-reading.md`](skills/mysql-client/references/explain-reading.md)
- [`perf-and-processes.md`](skills/mysql-client/references/perf-and-processes.md)

## Security

- The skill **refuses to put passwords on the command line.** `-p<password>` leaks via `ps`; the plugin uses login-paths, `~/.my.cnf`, or `--defaults-file` exclusively.
- The skill **runs a "where am I" probe before any query** and surfaces the answer (`@@hostname`, `@@read_only`, `USER()`, …) so you can spot a misrouted connection before it does damage.
- The skill **sets `--safe-updates` / `sql_safe_updates` on every session.** Even if you somehow bypassed the read-only stance, accidental WHERE-less `UPDATE`/`DELETE` is server-rejected.
- The subagent's allowlist forbids writes at the harness level — no `Edit`, no `Write`, no `Agent` recursion.
- When `.claude/mysql-client.local.md` is in play, the `connection_cmd` output is captured into a mode-600 tempfile via `>` redirection and **never enters the conversation transcript**. The tempfile is deleted at the end of every turn.

## Troubleshooting

- **Connection trouble, or first-time setup.** Run `/mysql-client:doctor` — it checks the client install, the settings file, that `connection_cmd` resolves, and that a probe actually connects, printing a fix for each failure.
- **The skill didn't fire when I expected.** Re-phrase the request to include a concrete MySQL trigger phrase (`mysql`, `EXPLAIN`, `SELECT`, schema name). The skill's description prioritises specificity over breadth.
- **The skill fired on something I wanted handled in plain Bash.** Tell Claude to skip the skill for this turn ("just run the literal command I wrote, don't engage the mysql-client skill").
- **`mysql_config_editor: command not found`.** You're on a MariaDB-only system (MariaDB ships no equivalent). Use `~/.my.cnf` with `chmod 600` instead.
- **`Access denied for user '...'@'<host>'`.** Wrong host part of the grant. Have someone who can run it execute `SHOW GRANTS FOR '<user>'@'%'` (or whichever pattern was set up).

## License

AGPL-3.0-only. The whole `claude-plugins` repo is AGPL for consistency. See [LICENSE](../../LICENSE).
