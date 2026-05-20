# Safety perimeter

The five cardinal rules from `SKILL.md`, with detail. They hold even when the user pushes back.

## 1. Never put passwords on the command line

`-p<password>` (no space) and `--password=<password>` both leak via `ps`, get audit-logged, and end up in `~/.bash_history`. Use a login-path, `~/.my.cnf`, or `--defaults-file` (see [`connecting.md`](connecting.md)).

If the user shows you a snippet with `-p$PASSWORD`, **stop** and rewrite it before running.

## 2. Default to `--safe-updates`

Pass `--safe-updates` (alias `-U`, alias `--i-am-a-dummy`) on every interactive or `--execute` invocation. It enables these server-side guards for the session:

- `UPDATE`/`DELETE` is rejected unless the `WHERE` clause uses a key column *or* there's a `LIMIT`.
- `SELECT` with no `LIMIT` is capped at `sql_select_limit` rows (default `1000`).
- Joins with the estimated row product over `max_join_size` (default `1000000`) are refused.

Tune in-session when needed; explain why in chat:

```sql
SET SESSION sql_select_limit = 10000;     -- larger exploratory pulls
SET SESSION max_join_size = 100000000;    -- justified join over 100M row product
```

`--safe-updates` is per-session and does not survive reconnect. Connection-pooling proxies (ProxySQL, RDS Proxy) may drop session state — re-set it after every reconnect:

```sql
SET SESSION sql_safe_updates = 1;
```

## 3. Probe before you query

Before any other SQL, run:

```sql
SELECT @@hostname, @@version, @@read_only, @@super_read_only, USER(), DATABASE();
```

Surface the answer verbatim in chat. The two checks that matter:

- **`@@hostname`** — confirms you hit the host you meant to. Critical when the user provides a DSN you can't visually verify; critical when reader/writer endpoints look similar (`my-cluster-ro.<id>.<region>.rds.amazonaws.com` vs `my-cluster.<id>.<region>.rds.amazonaws.com`).
- **`@@read_only` / `@@super_read_only`** — if either is `1`, the server itself refuses non-temporary writes from anyone but `SUPER`/`CONNECTION_ADMIN`. That's a hard backstop; if both are `0`, you have no backstop and `--safe-updates` is doing all the work.

Then run the safety set if we're against a writable server:

```sql
SET SESSION sql_safe_updates = 1;
SET SESSION max_execution_time = 30000;   -- MySQL: milliseconds, SELECT-only
-- MariaDB equivalent:
-- SET SESSION max_statement_time = 30;   -- seconds (DECIMAL), broader scope
```

`max_execution_time` (MySQL 5.7.4+) cancels `SELECT` statements past the limit. It **does not** cancel `UPDATE`, `DELETE`, or DDL — those run to completion. MariaDB's `max_statement_time` does cover more statement kinds.

## 4. No DDL, DML, `KILL`, `SET GLOBAL`, `FLUSH`, `RESET` without explicit confirmation

Out of scope for the skill. If the user asks for one of these, *restate* the proposed statement (with the schema/table named) and ask for confirmation. "Just this once" still requires the confirmation.

Specifically refuse to run, without per-statement confirmation:

- `CREATE` / `ALTER` / `DROP` / `RENAME` / `TRUNCATE` / `REPAIR` / `OPTIMIZE`
- `INSERT` / `UPDATE` / `DELETE` / `REPLACE`
- `KILL [QUERY|CONNECTION] <id>` — terminates either the current statement or the whole session
- `SET GLOBAL <var> = ...` — affects every other client
- `FLUSH` (any variant — privileges, hosts, tables, logs)
- `RESET MASTER`, `RESET SLAVE`, `RESET REPLICA`, `RESET QUERY CACHE`
- `LOAD DATA INFILE`, `SELECT ... INTO OUTFILE` — writes to the server's filesystem
- `CHANGE MASTER TO`, `CHANGE REPLICATION SOURCE TO`
- `INSTALL PLUGIN`, `UNINSTALL PLUGIN`

`SET SESSION` is fine — it only affects the current connection.

## 5. Always `LIMIT N` on exploratory selects

Never `SELECT * FROM unknown_table`. The cost of getting it wrong is paid in three places: client memory (especially without `--quick`), the network, and the server's buffer pool churn.

```sql
-- Bad
SELECT * FROM orders WHERE created_at > NOW() - INTERVAL 7 DAY;

-- Good
SELECT COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL 7 DAY;  -- gauge size first
SELECT * FROM orders WHERE created_at > NOW() - INTERVAL 7 DAY LIMIT 50;
```

If the user explicitly wants every row of a large table, use `--quick` to avoid buffering the result set client-side, *and* pipe to a file:

```bash
mysql --login-path=prod-reader --batch --quick -e \
  "SELECT * FROM orders WHERE created_at > NOW() - INTERVAL 7 DAY" \
  > orders_recent.tsv
```

## What "read-only" still affects

Even on a `@@read_only = 1` server, with `--safe-updates` on, your session can still:

- Acquire **shared metadata locks** (e.g. `EXPLAIN ANALYZE`, `SHOW CREATE TABLE` on a busy table). Long-held shared MDLs block writers from acquiring exclusive MDLs for things like `ALTER TABLE`. Don't run multi-second introspection on a table that's currently being altered.
- Mutate `performance_schema` counters. Harmless except that it means `events_statements_summary_by_digest` includes your queries — exclude them by digest when summarising.
- Generate large query-cache eviction (on servers that still have query cache; MySQL 8.0 removed it).
- Pin a row image in the binary log on the connection's read-view snapshot (with `REPEATABLE READ`). Long-lived sessions hold history; close idle sessions promptly.

## Long-lived investigation sessions

`wait_timeout` defaults to 28800s (8h). To keep an interactive session open across a long investigation:

```sql
SET SESSION wait_timeout = 7200;   -- 2 hours
```

Don't set the *global* `wait_timeout` — that's a `SET GLOBAL` write.

## `--one-database` (use sparingly)

`mysql ... --one-database <db>` filters incoming statements (typically from a `source`-loaded dump or a binlog stream) so only statements affecting `<db>` are executed. It's a *parsing* filter, not a connection scope — `USE other_db; UPDATE other_db.t ...` still gets filtered correctly, but `UPDATE other_db.t` issued bare can slip through in odd edge cases. Useful for replaying a single-DB slice of a multi-DB dump, but **don't rely on it as a safety boundary** in interactive sessions.

## Behind a connection pooler

ProxySQL / RDS Proxy can recycle the underlying connection and drop session-scoped state — your `SET SESSION sql_safe_updates = 1` silently evaporates after an idle gap. If results look off after a quiet interval, re-run the bootstrap probe and re-apply the session vars.
