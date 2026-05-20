# Performance schema, processes, replication

Read-only inspection of what the server is doing right now and what it's been doing.

## Processlist

What's running this instant:

```sql
SHOW FULL PROCESSLIST;            -- ; works in --table mode, use \G for wide rows
```

Programmatic / filterable:

```sql
SELECT id, user, host, db, command, time, state, LEFT(info, 200) AS query
FROM information_schema.processlist
WHERE command <> 'Sleep'                     -- skip idle
  AND time > 1                               -- only stuff running > 1s
ORDER BY time DESC;
```

Key columns:

- `command` — `Query`, `Sleep`, `Connect`, `Binlog Dump`, `Killed`. `Sleep` rows are idle pooled connections, not work.
- `time` — seconds in current state. For `Sleep`, time since last query.
- `state` — fine-grained phase: `Sending data`, `Sorting result`, `Waiting for table metadata lock`, `Locked`, etc.
- `info` — the SQL being executed. Truncated by default in `SHOW`; not truncated in `information_schema.processlist`.

`KILL <id>` (or `KILL QUERY <id>`) terminates a connection or its current statement respectively. Both are **writes** under the skill's safety perimeter — require user confirmation per id.

## `performance_schema` — what's worth querying read-only

### Top digests by total time (the slow-query proxy)

```sql
SELECT schema_name,
       digest,
       LEFT(digest_text, 120)                            AS digest_text,
       count_star                                        AS exec_count,
       ROUND(sum_timer_wait / 1e9, 2)                    AS sum_ms,
       ROUND(avg_timer_wait / 1e9, 2)                    AS avg_ms,
       ROUND(max_timer_wait / 1e9, 2)                    AS max_ms,
       ROUND(sum_rows_examined / GREATEST(count_star,1)) AS avg_examined,
       ROUND(sum_rows_sent     / GREATEST(count_star,1)) AS avg_sent,
       FIRST_SEEN, LAST_SEEN
FROM performance_schema.events_statements_summary_by_digest
WHERE schema_name IS NOT NULL
ORDER BY sum_timer_wait DESC
LIMIT 20;
```

Reading: high `sum_ms` = biggest total time spent; `avg_ms` shows whether it's high-frequency cheap queries or rare expensive ones. `avg_examined` >> `avg_sent` suggests missing indexes.

`digest_text` is the parameterised SQL (literals replaced with `?`). Two queries with different literals collapse to the same digest — that's the point.

The summary is per server start; `TRUNCATE TABLE performance_schema.events_statements_summary_by_digest` resets it, but that's a write — don't run without confirmation.

### Index usage — which indexes are pulling weight, which aren't

```sql
SELECT object_schema, object_name, index_name,
       count_fetch, count_insert, count_update, count_delete
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE object_schema = '<db>'
  AND index_name IS NOT NULL
ORDER BY count_fetch DESC;
```

`index_name = PRIMARY` shows pk usage; rows with `index_name IS NOT NULL AND count_fetch = 0` are *candidates* for being unused indexes — but only over a representative sample window. Don't recommend a drop based on a fresh server start.

### Lock waits

```sql
SELECT * FROM performance_schema.data_locks\G                -- current row/table locks held
SELECT * FROM performance_schema.data_lock_waits\G           -- who's blocking whom (MySQL 8.0+)
```

Requires `PROCESS` privilege. For older servers use `INFORMATION_SCHEMA.INNODB_LOCKS` / `INNODB_LOCK_WAITS` (deprecated in 8.0, removed eventually).

### Metadata locks (the `ALTER TABLE` blocker case)

```sql
SELECT object_type, object_schema, object_name,
       lock_type, lock_status,
       owner_thread_id, owner_event_id
FROM performance_schema.metadata_locks
WHERE owner_thread_id <> SYS.PS_THREAD_ID(CONNECTION_ID());
```

To get the SQL behind a thread holding a blocking MDL, join to `performance_schema.threads` → `events_statements_current`:

```sql
SELECT t.processlist_id, t.processlist_user, t.processlist_host,
       LEFT(esc.sql_text, 200) AS sql_text
FROM performance_schema.threads t
LEFT JOIN performance_schema.events_statements_current esc
       ON esc.thread_id = t.thread_id
WHERE t.thread_id IN (<thread_id_from_metadata_locks>);
```

## The `sys` schema (MySQL only; not in MariaDB)

Built on top of `performance_schema` with friendlier views. Worth knowing:

- `sys.statement_analysis` — same as the digest summary, pre-formatted.
- `sys.schema_table_statistics` — per-table read/write/lock stats.
- `sys.schema_unused_indexes` — exactly what it sounds like (caveat: same sample-window warning).
- `sys.innodb_buffer_stats_by_table` — what's hot in the buffer pool.
- `sys.processlist` — `information_schema.processlist` with extra columns (latencies, current statement progress).

Example:

```sql
SELECT * FROM sys.statement_analysis ORDER BY total_latency DESC LIMIT 10\G
```

MariaDB doesn't ship `sys`; the underlying `performance_schema` queries still work.

## Slow query log

Find its location and threshold:

```sql
SHOW VARIABLES LIKE 'slow_query_log%';     -- on/off, file path
SHOW VARIABLES LIKE 'long_query_time';     -- seconds
SHOW VARIABLES LIKE 'log_output';          -- FILE / TABLE / FILE,TABLE
SHOW VARIABLES LIKE 'log_queries_not_using_indexes';
```

- `log_output = 'TABLE'` → entries land in `mysql.slow_log` (queryable):
  ```sql
  SELECT start_time, query_time, lock_time, rows_examined, rows_sent,
         LEFT(sql_text, 200) AS sql_text
  FROM mysql.slow_log
  WHERE start_time > NOW() - INTERVAL 1 HOUR
  ORDER BY query_time DESC
  LIMIT 20;
  ```
- `log_output = 'FILE'` (the common case) → entries land on disk. The `mysql` client can't grep a file — exit to Bash with `Read` / `Grep`. Locations vary: `/var/log/mysql/slow.log`, `/var/lib/mysql/<host>-slow.log`, etc. The variable `slow_query_log_file` has the canonical path.

Cloud-managed MySQL exposes the slow log through provider APIs, not the local filesystem:

- **RDS / Aurora** — `mysql.slow_log` (TABLE output is the only option) plus CloudWatch / `aws rds download-db-log-file-portion`.
- **Cloud SQL** — Cloud Logging only; no `mysql.slow_log` table.
- **PlanetScale / serverless tiers** — usually only via the provider dashboard.

## Replication state (read-only)

### Replica status

MySQL 8.0.22+:

```sql
SHOW REPLICA STATUS\G                -- preferred
```

Older MySQL + MariaDB:

```sql
SHOW SLAVE STATUS\G                  -- alias retained in 8.0+ for compatibility
```

Critical fields:

| 8.0.22+                       | Older / MariaDB             | Meaning                                              |
|-------------------------------|-----------------------------|------------------------------------------------------|
| `Replica_IO_Running`          | `Slave_IO_Running`          | `Yes` / `No` / `Connecting`. `No` = stuck.            |
| `Replica_SQL_Running`         | `Slave_SQL_Running`         | `Yes` / `No`. `No` with `Last_SQL_Error` set → bad.   |
| `Seconds_Behind_Source`       | `Seconds_Behind_Master`     | Lag in seconds; `NULL` = SQL thread not running.      |
| `Last_IO_Error` / `Last_SQL_Error` | same                  | First port of call when either flag is `No`.          |
| `Retrieved_Gtid_Set` / `Executed_Gtid_Set` | same          | GTID-based positions; gaps = unapplied transactions.  |

`Seconds_Behind_Source` is a poor lag metric on busy replicas (it measures the SQL thread's clock skew vs the binlog timestamp). For monitoring, prefer GTID set diffs or a heartbeat table; for a one-off "is the replica caught up?", this is fine.

### Connected replicas (from the source)

```sql
-- MySQL 8.0.22+
SHOW REPLICAS;
-- Older
SHOW SLAVE HOSTS;
```

### Group replication

```sql
SELECT * FROM performance_schema.replication_group_members\G
SELECT * FROM performance_schema.replication_applier_status\G
SELECT * FROM performance_schema.replication_connection_status\G
```

`MEMBER_STATE` should be `ONLINE` on every node. `RECOVERING` is normal during join; persisting `RECOVERING` is bad.

### GTID positions

```sql
SELECT @@global.gtid_executed AS executed,
       @@global.gtid_purged   AS purged;
```

Useful for "can replica X catch up from binlog?" — if `gtid_purged` on the source moved past where the replica is, the replica needs a fresh dump.

## InnoDB engine status

```sql
SHOW ENGINE INNODB STATUS\G
```

One big text blob with sections: `LATEST DETECTED DEADLOCK`, `TRANSACTIONS`, `FILE I/O`, `BUFFER POOL`, `ROW OPERATIONS`. Read selectively; the deadlock and transaction sections are the most actionable. `\G` is required — the output is one row, one column.
