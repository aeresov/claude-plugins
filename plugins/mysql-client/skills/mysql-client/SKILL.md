---
name: mysql-client
description: Drive the Linux `mysql` (mysql-client) CLI safely against MySQL or MariaDB databases. Activate when the user asks Claude to connect to one, inspect schema, run a SELECT, read EXPLAIN or EXPLAIN ANALYZE output, debug a slow query, look at SHOW PROCESSLIST, performance_schema, or the slow log, or shares a connection string, login-path, DSN, or `~/.my.cnf` reference. Read-only-first by design — never passes passwords on the command line, defaults to `--safe-updates`, runs a where-am-I probe before queries, and bounces DDL, DML, migration, KILL, and SET GLOBAL requests back to the user instead of running them.
---

# mysql-client

Make `mysql` calls effective, machine-readable, and safe. The skill assumes Claude already knows SQL; it owns CLI ergonomics, the read-only safety perimeter, and the methodology for investigating a database from a shell.

The default stance is **read-only investigation**. Schema changes, migrations, and DML are out of scope — the skill names them and hands them back to the user.

## Connection discovery

Before asking the user how to connect, check `.claude/mysql-client.local.md` in the project root. If it exists, its YAML frontmatter has a `connection_cmd` field whose stdout is the body of a `[client]`-section INI — the skill provisions a mode-600 tempfile from it and uses `mysql --defaults-file=<tmp>` for every call this turn. Examples (vault, AWS Secrets Manager, RDS IAM auth, SOPS, 1Password): [`references/local-settings.md`](references/local-settings.md).

Flow when the file is present:

1. **Parse the frontmatter.** Verify `connection_cmd` is set and non-empty. Missing or malformed → tell the user and stop. Do not guess.
2. **Provision lazily** (first time mysql is needed this turn):
   ```bash
   umask 077
   tmp="$(mktemp --suffix=.cnf)"
   { <connection_cmd> ; } > "$tmp" 2> /tmp/mysql-client-cmderr.$$
   ```
   The stdout bytes **must not** appear in your output — they contain a password. Do not `cat "$tmp"`. Stderr is fine to surface.
3. **Non-zero exit or empty `$tmp`** → show the stderr from `/tmp/mysql-client-cmderr.$$`, `rm -f "$tmp" /tmp/mysql-client-cmderr.$$`, **stop**.
4. **Use the tempfile** for every mysql call this turn: `mysql --defaults-file="$tmp" --safe-updates -e '...'`. Combine with the safety perimeter below.
5. **Dispatching `mysql-investigator` in settings-file mode** → pass the tempfile path to the agent as the connection method (e.g. "use `mysql --defaults-file=/tmp/abc.cnf`"). The skill — not the agent — owns the tempfile's lifecycle.
6. **Cleanup at end of turn:** `rm -f "$tmp" /tmp/mysql-client-cmderr.$$`, whether you dispatched the agent or not.

If `.claude/mysql-client.local.md` is absent, ask the user for a connection method (a login-path name, a `--defaults-file` path, or a full `mysql` command prefix) — see [`references/connecting.md`](references/connecting.md).

## Safety perimeter (non-negotiable)

These rules hold even if the user pushes back. Surface the rule, ask the user to confirm with explicit wording, never quietly bypass.

1. **Never put passwords on the command line.** `-p<password>` (`-psecret`) is visible to anyone running `ps`. Use a `mysql_config_editor` login-path, a `[client]` section in `~/.my.cnf`, or `--defaults-file=<path>`. See [`references/connecting.md`](references/connecting.md).
2. **Default to `--safe-updates`** (alias `-U`, alias `--i-am-a-dummy`) for every interactive or `--execute` session. It blocks WHERE-less / LIMIT-less `UPDATE` and `DELETE`, and caps unbounded `SELECT` via `sql_select_limit` (default 1000). Bump with `SET SESSION sql_select_limit = 10000;` if you genuinely need more rows — name the reason in chat.
3. **Run the "where am I" probe before any other query.** Surface the answer to the user verbatim:
   ```sql
   SELECT @@hostname, @@version, @@read_only, @@super_read_only, USER(), DATABASE();
   ```
   If `@@read_only` or `@@super_read_only` is `1`, write attempts will fail anyway — say so. If both are `0`, set `SET SESSION sql_safe_updates = 1; SET SESSION max_execution_time = 30000;` before any further work.
4. **No DDL, no DML, no `KILL`, no `SET GLOBAL`, no `FLUSH`, no `RESET`, no `/etc/mysql/*` edits without explicit per-statement user confirmation.** If asked, restate the proposed statement and ask before running. "Just this once" still requires the confirmation.
5. **Always `LIMIT N`** on exploratory `SELECT`s. Never `SELECT *` from a table whose row count you haven't checked. If the user wants "all rows", run `SELECT COUNT(*)` first and confirm.

## Inline or dispatch?

- **One small query → run inline.** Use the skill's guidance directly via `Bash` + `mysql`.
- **Multi-step investigation that needs 5+ `mysql` calls, or that would produce bulk output (multi-table schema dumps, EXPLAIN trees combined with index inspection, processlist + slow log triage) → dispatch the [`mysql-investigator`](../../agents/mysql-investigator.md) subagent.** Pass it the connection method (login-path name or `--defaults-file` path or full `mysql` command prefix) and the question. The agent returns a focused writeup and keeps the main context clean.

The subagent has the same safety perimeter baked in and refuses writes.

## Output format — pick once per query

| Want                                                  | Use                                                  |
|-------------------------------------------------------|------------------------------------------------------|
| Tabular result for a human                            | default `--table` (interactive) or pipe to `column`  |
| Machine-parseable result (TSV)                        | `--batch` / `-B`, optionally `--skip-column-names`   |
| Wide rows or single-row inspection                    | `--vertical` / trailing `\G`                         |
| Values containing tabs/newlines, no escaping          | `--raw` / `-r` (only with `--batch`; treat carefully)|
| Large result set without blowing up client memory     | `--quick` / `-q`                                     |

See [`references/output-formats.md`](references/output-formats.md) for the full chooser including pager, encoding, and the `mysqlsh --json` escape hatch.

## References

Load the file matching the current step; ignore the rest.

- [`references/connecting.md`](references/connecting.md) — login-paths, `~/.my.cnf`, `--defaults-file`, sockets vs TCP, SSL, verify-connection snippet, RDS/Aurora/MariaDB quirks.
- [`references/local-settings.md`](references/local-settings.md) — `.claude/mysql-client.local.md` format and `connection_cmd` examples (vault, AWS Secrets Manager, RDS IAM auth, SOPS, 1Password).
- [`references/safety-perimeter.md`](references/safety-perimeter.md) — deep version of the five rules above; `--safe-updates` semantics; session vars; what "read-only" still affects.
- [`references/output-formats.md`](references/output-formats.md) — every output flag, pager, encoding, `mysqlsh --json`.
- [`references/schema-introspection.md`](references/schema-introspection.md) — INFORMATION_SCHEMA cookbook + `SHOW` shortcuts; FK graph, indexes, partitions, views, routines, triggers.
- [`references/explain-reading.md`](references/explain-reading.md) — `EXPLAIN`, `EXPLAIN FORMAT=JSON`, `EXPLAIN ANALYZE`; type-hierarchy ladder; reading `rows`, `filtered`, `Extra`; antipatterns; optimizer trace.
- [`references/perf-and-processes.md`](references/perf-and-processes.md) — `SHOW PROCESSLIST`, `performance_schema` digests, lock waits, slow log discovery, read-only replication checks.

## When to step out of this skill

- **DDL, migrations, DML, schema changes.** Name the operation, hand it back to the user. The skill won't help silently.
- **Server admin** (users/grants, replication writes, `mysqldump` backups, server vars). Out of scope; tell the user this skill doesn't cover it.
- **Non-MySQL databases.** Postgres, SQLite, Redis, etc. — wrong skill.
- **Driver/library code** (Python, Node, Go, Java). This skill is about the `mysql` binary specifically. Switch to whatever guidance covers the driver.
