# Schema introspection

Two routes: `SHOW` commands (fast, ergonomic for single questions) and `INFORMATION_SCHEMA` queries (filterable, joinable, the right hammer for anything across multiple tables).

Always filter `INFORMATION_SCHEMA` queries by `table_schema`. Without a filter you scan metadata for every database on the server; on large multi-tenant servers that's expensive enough to register on the slow log.

## `SHOW` shortcuts

| You want                                | Run                                                  |
|-----------------------------------------|------------------------------------------------------|
| List databases visible to you           | `SHOW DATABASES;`                                    |
| List tables in a database               | `SHOW TABLES FROM <db>;` (or `USE <db>; SHOW TABLES;`) |
| Table sizes / engines / row estimates   | `SHOW TABLE STATUS FROM <db> LIKE 'pat%';`           |
| Canonical schema of a table             | `SHOW CREATE TABLE <db>.<t>\G`                       |
| Database default charset/collation      | `SHOW CREATE DATABASE <db>;`                         |
| Columns of a table                      | `SHOW FULL COLUMNS FROM <db>.<t>;`                   |
| Indexes of a table                      | `SHOW INDEX FROM <db>.<t>;`                          |
| Views in a database                     | `SELECT table_name FROM information_schema.views WHERE table_schema='<db>';` (no `SHOW VIEWS`) |
| Triggers in a database                  | `SHOW TRIGGERS FROM <db>;`                           |
| Stored routines                         | `SHOW PROCEDURE STATUS WHERE Db = '<db>';` / `SHOW FUNCTION STATUS WHERE Db = '<db>';` |
| Server variables (config)               | `SHOW VARIABLES LIKE 'pat%';`                        |
| Server status counters                  | `SHOW GLOBAL STATUS LIKE 'pat%';`                    |
| Open table cache info                   | `SHOW OPEN TABLES FROM <db>;`                        |
| Storage engines                         | `SHOW ENGINES;`                                      |
| Character sets and collations           | `SHOW CHARACTER SET;` / `SHOW COLLATION;`            |

Use `\G` on `SHOW CREATE TABLE` — the output is one wide row and the default `--table` mode mangles it.

## INFORMATION_SCHEMA cookbook

### Tables in a schema, biggest first

```sql
SELECT table_name,
       engine,
       table_rows,                                    -- estimate, not exact
       ROUND((data_length + index_length) / 1024 / 1024, 2) AS total_mb,
       ROUND(data_length  / 1024 / 1024, 2)            AS data_mb,
       ROUND(index_length / 1024 / 1024, 2)            AS index_mb
FROM information_schema.tables
WHERE table_schema = '<db>'
ORDER BY data_length + index_length DESC
LIMIT 30;
```

`table_rows` is an InnoDB **estimate** that can be off by 10× or more for tables under heavy churn. For an exact count use `SELECT COUNT(*) FROM <t>` — but expect a full clustered-index scan and a non-trivial response time on big tables.

### Find every column matching a name pattern

```sql
SELECT table_name, column_name, column_type, is_nullable
FROM information_schema.columns
WHERE table_schema = '<db>'
  AND column_name LIKE '%email%'
ORDER BY table_name, ordinal_position;
```

Useful for "where does PII live?" — pair with `%phone%`, `%ssn%`, `%password%`, etc.

### Foreign-key graph for a schema

```sql
SELECT kcu.table_name        AS src_table,
       kcu.column_name       AS src_column,
       kcu.referenced_table_name  AS dst_table,
       kcu.referenced_column_name AS dst_column,
       rc.delete_rule, rc.update_rule
FROM information_schema.key_column_usage kcu
JOIN information_schema.referential_constraints rc
  ON rc.constraint_schema = kcu.constraint_schema
 AND rc.constraint_name   = kcu.constraint_name
WHERE kcu.table_schema = '<db>'
  AND kcu.referenced_table_name IS NOT NULL
ORDER BY src_table, src_column;
```

This is the canonical "how do these tables relate?" query. For a single table's incoming FKs (who references me?), filter by `kcu.referenced_table_name = '<t>'`.

### Indexes on a table

```sql
SELECT index_name,
       seq_in_index,
       column_name,
       non_unique,                                 -- 0 = unique, 1 = not unique
       cardinality,                                -- estimate; refresh with ANALYZE TABLE
       index_type                                  -- BTREE / HASH / FULLTEXT / SPATIAL
FROM information_schema.statistics
WHERE table_schema = '<db>'
  AND table_name   = '<t>'
ORDER BY index_name, seq_in_index;
```

Reading: rows are *index columns*, not indexes. Group by `index_name` to see each composite index's column order. Column order matters for which `WHERE` predicates can hit the index (leftmost-prefix rule).

`cardinality` is an estimate maintained by InnoDB statistics; it can be stale. To refresh: `ANALYZE TABLE <db>.<t>;` — but that's a write, so requires user confirmation.

### Partitions

```sql
SELECT partition_name,
       partition_method,
       partition_expression,
       partition_description,
       table_rows
FROM information_schema.partitions
WHERE table_schema = '<db>'
  AND table_name   = '<t>'
  AND partition_name IS NOT NULL
ORDER BY partition_ordinal_position;
```

Non-partitioned tables have a single NULL-`partition_name` row in this table — that's why the filter is there.

### Views

```sql
SELECT table_name,
       is_updatable,
       definer,
       security_type,
       view_definition
FROM information_schema.views
WHERE table_schema = '<db>';
```

`view_definition` is the canonical SELECT body (rewritten by the optimizer). For the original `CREATE VIEW` text, use `SHOW CREATE VIEW <db>.<v>\G`.

### Stored routines

```sql
SELECT routine_name, routine_type, data_type, security_type, definer, last_altered
FROM information_schema.routines
WHERE routine_schema = '<db>'
ORDER BY routine_type, routine_name;
```

Body: `SHOW CREATE PROCEDURE <db>.<p>\G` / `SHOW CREATE FUNCTION <db>.<f>\G`. (No body column in `information_schema.routines` by default — it's `routine_definition` but truncated by some servers.)

### Triggers

```sql
SELECT trigger_name,
       event_manipulation,                          -- INSERT / UPDATE / DELETE
       event_object_table,
       action_timing,                               -- BEFORE / AFTER
       definer
FROM information_schema.triggers
WHERE trigger_schema = '<db>'
ORDER BY event_object_table, trigger_name;
```

### Character set / collation per table

```sql
SELECT t.table_name,
       t.table_collation,
       ccsa.character_set_name
FROM information_schema.tables t
JOIN information_schema.collation_character_set_applicability ccsa
  ON ccsa.collation_name = t.table_collation
WHERE t.table_schema = '<db>';
```

Mixed collations across tables is one of the most-common causes of `Illegal mix of collations` errors during joins.

## Cost of INFORMATION_SCHEMA queries

- **Filter by `table_schema`** every time. Without it, MySQL opens every table's metadata.
- Some columns are computed lazily (e.g. `table_rows` for InnoDB); selecting them on huge schemas can be slow.
- `INFORMATION_SCHEMA.COLUMNS` is particularly heavy — it's per-column rather than per-table.
- The `sys` schema (built-in on MySQL 5.7+) exposes cached / formatted views over `INFORMATION_SCHEMA` + `performance_schema` that are sometimes cheaper to query. Example: `SELECT * FROM sys.schema_table_statistics WHERE table_schema = '<db>' ORDER BY rows_fetched DESC LIMIT 20;` — index of "which tables are read the most".

## MariaDB notes

- All of the above works on MariaDB, with caveats:
  - `information_schema.user_statistics`, `client_statistics`, `index_statistics`, `table_statistics` exist *only when* the server has `userstat=ON` set. Default is off.
  - MariaDB lacks the `sys` schema. Use `INFORMATION_SCHEMA` / `performance_schema` directly.
  - MariaDB has additional system catalog views under `information_schema` (e.g. `KEY_PERIOD_USAGE`, `PERIOD_USAGE`) for application-time periods (SQL:2011), not present in MySQL.
