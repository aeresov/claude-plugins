# mysql-url-to-cnf

Converts a `mysql://` (or `mariadb://`) URL into a MySQL `[client]` option-file block. Part of the [`mysql-client`](../../) Claude Code plugin: its connection-discovery flow pipes a project's `connection_cmd` output through this converter into the file `mysql --defaults-file` reads.

Pure standard library, no runtime dependencies — the plugin runs it as `python3 src/mysql_url_to_cnf/__init__.py`, reading the URL on stdin and writing the INI on stdout. Non-URL input exits non-zero.

## Development

```bash
uv sync --group dev
uv run pytest -q
```
