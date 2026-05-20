# Reading `EXPLAIN`

Three forms exist; pick deliberately.

| Form                          | Runs the query? | Output         | Use for                                              |
|-------------------------------|-----------------|----------------|------------------------------------------------------|
| `EXPLAIN <stmt>`              | No              | Tabular plan   | First-look. Cheap. Shows the *intended* plan.        |
| `EXPLAIN FORMAT=JSON <stmt>`  | No              | Structured JSON, includes cost estimate | Comparing two plan options; reading nested operations |
| `EXPLAIN ANALYZE <stmt>`      | **Yes**         | Iterator tree with actual rows and time | When estimates and reality might differ; MySQL 8.0.18+ |

`EXPLAIN ANALYZE` is **not** safe on `UPDATE`/`DELETE`/`INSERT` — it executes them. Only run it on `SELECT` (or a `WITH ... SELECT`). The skill's safety perimeter forbids running it on writes regardless.

MariaDB equivalents: `ANALYZE SELECT <stmt>` (since 10.1) and `ANALYZE FORMAT=JSON SELECT <stmt>`. The output is similar but not identical.

Format: `EXPLAIN` output is wide; pipe to `\G`:

```sql
EXPLAIN SELECT u.email, COUNT(*) FROM users u JOIN orders o ON o.user_id = u.id
WHERE u.created_at > '2026-01-01' GROUP BY u.email\G
```

## Column-by-column (tabular form)

| Column          | Meaning                                                                                          |
|-----------------|---------------------------------------------------------------------------------------------------|
| `id`            | Step number. Higher `id` = inner; same `id` = same join nest.                                    |
| `select_type`   | `SIMPLE`, `PRIMARY`, `SUBQUERY`, `DEPENDENT SUBQUERY`, `DERIVED`, `UNION`, `MATERIALIZED`        |
| `table`         | Table or alias (or `<subquery2>` / `<derived3>`).                                                |
| `partitions`    | Which partitions are accessed; `NULL` for non-partitioned. Partition pruning success indicator.  |
| `type`          | Access method. See ladder below — the most informative column.                                   |
| `possible_keys` | Indexes the optimizer considered.                                                                |
| `key`           | The index actually chosen. `NULL` = full scan (`type=ALL`) or no index could help.               |
| `key_len`       | Bytes of the index used. Lower than the index's full width = composite-index suffix unused.      |
| `ref`           | What's compared with the chosen index: constants, columns from prior joined tables, `func`.      |
| `rows`          | Estimated rows examined at this step. Cumulative cost = product across rows of the plan.         |
| `filtered`      | `%` of `rows` estimated to survive the `WHERE`. Low filtered + high rows = wasted I/O.           |
| `Extra`         | Extra hints. See below — the second-most-informative column.                                     |

### Type ladder (worst → best)

`ALL` → `index` → `range` → `index_subquery` → `unique_subquery` → `index_merge` → `ref_or_null` → `ref` → `fulltext` → `eq_ref` → `const` → `system`

In practice, target `ref`, `eq_ref`, `range`, or `const`. `ALL` on anything but the smallest table is a full scan. `index` (covering scan of the index, not the table) is sometimes acceptable for very small or covering cases — verify `Extra` says `Using index`.

### `Extra` cheat sheet

| Token                    | Meaning                                                                      |
|--------------------------|------------------------------------------------------------------------------|
| `Using index`            | Covering — index alone satisfies the query. Good.                            |
| `Using index condition`  | Index Condition Pushdown — extra WHERE predicates evaluated at the index level. Good. |
| `Using where`            | Server-side filter after the storage engine returned rows. Often unavoidable. |
| `Using filesort`         | Result was sorted in memory or on disk. ORDER BY isn't covered by the index. |
| `Using temporary`        | Intermediate result spooled to a tmp table (often for GROUP BY/UNION).        |
| `Using join buffer (...)` | Join without index on the inner side; Block Nested Loop or Hash Join.        |
| `Range checked for each record` | Bad sign; per-row planning, very expensive.                              |
| `Impossible WHERE`       | Constant-folded to no rows. The query won't return anything.                 |
| `Select tables optimized away` | Aggregated to a constant without touching tables (e.g. `MIN(indexed_col)`). |

## Antipatterns checklist

When reading a plan, scan for these:

1. **`type=ALL` on a non-tiny table** → no usable index. Either the WHERE doesn't match a leftmost index prefix, or there's no index at all.
2. **`rows` much larger than `rows × filtered`** → low selectivity. The optimizer expects to discard most of what it reads. Often fixable with a better composite index.
3. **`Using filesort` + `Using temporary`** → GROUP BY or ORDER BY without an index-aligned plan. The classic "make this an indexed sort" target.
4. **`Using join buffer (Block Nested Loop)`** → join condition not indexed on the inner side. Add the index.
5. **`DEPENDENT SUBQUERY`** → correlated subquery executed per outer row. Often rewritable as a JOIN.
6. **`key_len` smaller than the chosen index width** → composite index suffix unused. The WHERE doesn't drive the rest of the index columns.
7. **`possible_keys` non-empty but `key=NULL`** → the optimizer rejected every candidate (usually because the predicate isn't selective enough; sometimes because stats are stale — `ANALYZE TABLE` would refresh).
8. **`Extra: Range checked for each record`** → catastrophic; deserves immediate attention.

## `EXPLAIN FORMAT=JSON`

Use when:

- You want the optimizer's cost estimate (`query_cost`) to compare two query rewrites.
- The plan is deeply nested (subqueries / derived tables) and the flat table form is hard to read.
- You want to see things flat-EXPLAIN doesn't show: `used_columns`, `attached_condition`, `index_condition`, `using_join_buffer` details.

```sql
EXPLAIN FORMAT=JSON SELECT ...\G
```

Read it top-down; `query_block` → `nested_loop` → per-table sections.

## `EXPLAIN ANALYZE`

```sql
EXPLAIN ANALYZE SELECT ...\G
```

Output is an iterator tree (rooted at the top). Each node shows:

- `actual time=<first_row>..<last_row>` — when the first row was produced and when the last one was.
- `rows=<actual>` — actual rows produced.
- `loops=<n>` — how many times this node ran (for inner sides of joins).

Compare against the planner's `rows` estimate; large discrepancies are stale stats or misestimated selectivity.

**`EXPLAIN ANALYZE` runs the query.** It can be slow. It acquires the same locks the query would. Do not run on a write statement; do not run on a long-running SELECT on a busy table without an `LIMIT` or a `max_execution_time` guard.

## Cardinality and statistics

```sql
SHOW INDEX FROM <db>.<t>;
```

The `Cardinality` column is the optimizer's estimate of distinct values for each index. Stale stats can make the optimizer pick a worse plan; if `EXPLAIN` looks wrong and stats might be stale, `ANALYZE TABLE <db>.<t>` refreshes them — but that's a write, so it requires user confirmation per the safety perimeter.

## Optimizer trace (debugging "why this plan?")

```sql
SET SESSION optimizer_trace = 'enabled=on';
SELECT ...;          -- run the actual query (read-only)
SELECT trace FROM information_schema.optimizer_trace\G
SET SESSION optimizer_trace = 'enabled=off';
```

Output is a multi-KB JSON tree explaining every consideration: which indexes were rejected and why, the cost model's numbers, range-scan considerations. Heavy reading. Useful when you can't otherwise explain a counterintuitive plan choice.

## Prepared statement plans (`EXPLAIN FOR CONNECTION`)

Sometimes the user's app is running a query with `?` parameters and you can't reproduce its plan because literal values are different.

```sql
-- in the user's session, get connection id:
SELECT CONNECTION_ID();
-- in your session, while their query is running:
EXPLAIN FOR CONNECTION <id>;
```

Read-only — `FOR CONNECTION` doesn't run anything; it inspects the plan currently in use by another session. Requires `PROCESS` privilege.
