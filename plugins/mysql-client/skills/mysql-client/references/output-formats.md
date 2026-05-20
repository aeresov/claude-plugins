# Output formats

`mysql` has five output modes; pick the one whose audience matches. Wrong pick wastes tokens or breaks parsers.

## Mode chooser

| Flag                              | What you get                                                  | Use for                                          |
|-----------------------------------|---------------------------------------------------------------|--------------------------------------------------|
| (none, interactive)               | Boxed `--table` output                                        | Human inspection from a terminal                 |
| `--execute` / `-e` (no other)     | `--table` if stdout is a TTY, `--batch` if not                | Auto-detected; usually fine                      |
| `--table` / `-t`                  | Forced boxed table                                            | Forcing human-readable in a piped context        |
| `--batch` / `-B`                  | TSV: tab-separated, one row per line, headers on row 1        | Parsing in shell / `awk` / `jq`-via-pipe         |
| `--batch --skip-column-names`     | TSV without the header row                                    | Pure data streams; column names already known    |
| `--batch --raw` / `-B -r`         | TSV with NO escaping of special chars                         | Only when values can't contain tabs/newlines     |
| `--vertical` / `-E` or `\G`       | One column per line, "asterisk" row separators                | Wide rows, single-row inspection, `EXPLAIN`      |
| `--xml`                           | XML envelope                                                  | Rare — only for tools that demand it             |
| `--html`                          | HTML table                                                    | Rarer — emailable result sets                    |
| `--silent` / `-s`                 | One `-s`: drop "Welcome" + timing. Two `-s`s: drop column sep | Cleaner script output                            |
| `--silent --silent`               | TSV without column header (same as `--skip-column-names`)     | —                                                |

Combine flags as needed:

```bash
mysql --login-path=prod-reader -BN -e \
  "SELECT id, email FROM users WHERE deleted_at IS NULL LIMIT 100"
# -B  = --batch (TSV)
# -N  = --skip-column-names (no header)
```

## TSV gotchas

- **`NULL` is printed as the literal string `NULL`.** Distinguishing it from the string `'NULL'` requires `--raw` (which then doesn't escape — opposite problem) or wrapping the SELECT: `IFNULL(col, '<<NULL>>')`.
- **Tabs and newlines inside text fields** get backslash-escaped (`\t`, `\n`, `\\`) in `--batch` mode. `--raw` disables the escaping but breaks line-per-row parsing if any value contains a newline. Use `--batch` (the default escape) and post-process if downstream tools handle escapes; otherwise wrap values: `REPLACE(REPLACE(col, '\t', ' '), '\n', ' ')`.
- **No JSON output.** `mysql` itself doesn't produce JSON. Options: use `mysqlsh -- --json=raw` (MySQL Shell, separate package) or build JSON server-side with `JSON_OBJECT()` / `JSON_ARRAYAGG()` and pull a single column:
  ```sql
  SELECT JSON_ARRAYAGG(JSON_OBJECT(
    'id', id, 'email', email, 'created_at', created_at
  )) FROM users LIMIT 100;
  ```
  Pulled with `mysql -BN -e '...' | jq .`, you have a valid JSON array.

## Vertical mode (`-E` / `\G`)

Two equivalent ways:

```bash
mysql --login-path=prod-reader --vertical -e "SHOW CREATE TABLE orders"
mysql --login-path=prod-reader -e "SHOW CREATE TABLE orders\G"   # \G terminator instead of ;
```

`\G` is per-statement; `--vertical` is per-session. Use `\G` when only the last statement should be vertical (very common for `EXPLAIN\G`, `SHOW STATUS\G`).

## Encoding

```bash
mysql --default-character-set=utf8mb4 ...
```

or set it in `~/.my.cnf`:

```ini
[client]
default-character-set=utf8mb4
```

Without this the client may negotiate `latin1` and emoji / non-Latin scripts render as `?`. The server side connection character set is independent of the table's collation — `utf8mb4` on the connection makes the client correctly decode whatever the server stored.

For BINARY/BLOB columns, decoded display is usually wrong anyway — wrap with `HEX(col)` or pipe to `xxd`.

## Pager (interactive only)

For long result sets in an interactive session:

```sql
\P less -SFXR
SELECT * FROM big_table LIMIT 5000;
\n                          -- turn pager off
```

Or set it once in `~/.my.cnf`:

```ini
[mysql]
pager=less -SFXR
```

`less` flags: `-S` no line-wrap (key for wide tables), `-F` quit immediately if one screenful, `-X` don't clear the screen on exit, `-R` raw control chars (color).

Pager is a CLI feature; it does nothing in `--execute` / `--batch` mode.

## Streaming large result sets (`--quick` / `-q`)

By default `mysql` buffers the whole result set client-side before printing the first row. Two problems with that on big results: client OOMs, and you can't tail-pipe.

```bash
mysql --login-path=prod-reader --batch --quick \
  -e "SELECT * FROM events WHERE day = '2026-05-19'" \
  | head -100
```

`--quick` keeps server-side cursor active for the duration of the read; you can't run another query on the same session until the current cursor drains. Don't combine `--quick` with `--vertical` for huge sets — vertical mode triples line count.

## Heredoc + script patterns

One-off multi-statement script, no temp file:

```bash
mysql --login-path=prod-reader -BN <<'SQL'
SET SESSION sql_safe_updates = 1;
SET SESSION max_execution_time = 30000;
SELECT @@hostname, @@read_only;
SELECT table_schema, COUNT(*) FROM information_schema.tables GROUP BY table_schema;
SQL
```

Quote the heredoc tag (`<<'SQL'`) to disable shell variable expansion inside — keeps `$var` literal, which is almost always what SQL wants.

For sourcing a `.sql` file:

```bash
mysql --login-path=prod-reader -BN < /path/to/queries.sql
# inside an interactive session:
# mysql> source /path/to/queries.sql
```

## Exit codes

`mysql` exits non-zero on connection errors and SQL errors. In a script:

```bash
if ! mysql --login-path=prod-reader -BN -e 'SELECT 1' > /tmp/out; then
  echo "mysql failed: $(cat /tmp/out)" >&2
  exit 1
fi
```

`--force` / `-f` continues on SQL errors (logging them to stderr) instead of stopping; use only when you genuinely want best-effort execution across many statements.
