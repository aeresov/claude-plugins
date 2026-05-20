# CLAUDE.md

Developer notes for working inside `mysql-client`. User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## What this plugin is

A bundle that makes Claude an effective and *safe* user of the Linux `mysql` (mysql-client) CLI against MySQL or MariaDB servers. It is **read-only-first** by design — it teaches investigation, not modification. No MCP server; Claude drives `mysql` via Bash. The plugin contributes a skill, a subagent, the `/mysql-client:setup` and `/mysql-client:doctor` commands, a `mysql://` URL→INI converter, and an optional per-project settings file.

## Layout

- `skills/mysql-client/SKILL.md` — entry point. Owns the safety perimeter, the "inline vs subagent" dispatch rule, and a tiny output-format chooser. Points at the references below.
- `skills/mysql-client/references/*.md` — seven reference docs. Each is reference-style (purpose → command → gotcha), not tutorial. Files are loaded on demand by Claude as a step needs them, so each one is self-sufficient.
  - `connecting.md` — login-paths, `~/.my.cnf`, `--defaults-file`, sockets vs TCP, SSL, verify-connection probe, provider quirks.
  - `local-settings.md` — `.claude/mysql-client.local.md` format; `connection_cmd` emits a `mysql://` URL; examples (make target, vault, AWS Secrets Manager, SOPS, 1Password) and how the URL becomes a connection.
  - `safety-perimeter.md` — deep version of the five cardinal rules; `--safe-updates` semantics; session vars; what "read-only" still affects.
  - `output-formats.md` — every output flag; encoding; pager; `--quick`; heredoc patterns; the no-JSON workaround.
  - `schema-introspection.md` — `SHOW` shortcuts + `INFORMATION_SCHEMA` cookbook. Tables-by-size, column search, FK graph, indexes, partitions, views, routines, triggers.
  - `explain-reading.md` — `EXPLAIN` / `EXPLAIN FORMAT=JSON` / `EXPLAIN ANALYZE`; type-hierarchy ladder; `Extra` cheat sheet; antipatterns; optimizer trace; `EXPLAIN FOR CONNECTION`.
  - `perf-and-processes.md` — processlist, `performance_schema` digests, lock waits, metadata locks, `sys` schema, slow log discovery, replication-state reads, InnoDB engine status.
- `agents/mysql-investigator.md` — context-isolated read-only investigator. Tools: `Bash`, `Read`, `Grep`, `Glob` (no `Edit` / `Write` / `NotebookEdit` / `Agent` — write-refusal is enforced at the allowlist level). Same safety perimeter as the skill, duplicated rather than referenced (subagents don't share the parent's skill context). The agent **does not** read `.claude/mysql-client.local.md` — the calling Claude resolves `connection_cmd` and passes the agent a `--defaults-file=<tmp>` path.
- `scripts/mysql-url-to-cnf/` — uv project (`uv_build`, dev dep `pytest`) for the URL→INI converter. `src/mysql_url_to_cnf/__init__.py` is `url_to_cnf()` (pure, raises `ValueError`) + `main()` (stdin→stdout); pure stdlib, zero runtime deps, SPDX header. The skill and `/mysql-client:doctor` run it as `python3 …/src/mysql_url_to_cnf/__init__.py` — no venv at runtime; it exits non-zero on non-URL input so a broken command fails loudly. `tests/` is the pytest suite, run by CI and `make test`.
- `commands/setup.md` + `commands/doctor.md` — `/mysql-client:setup` (interactive configurator: picks where the connection URL comes from, writes the settings file, fixes `.gitignore`) and `/mysql-client:doctor` (health check). Both read `setup-checklist.md` for the 6 checks so they can't drift. Neither is privileged — `setup` never connects; `doctor` runs the live checks 4–5.
- `setup-checklist.md` — shared checklist (6 checks + remediation text) at the plugin root, so it isn't itself a slash command. `doctor` reports all 6; `setup` runs the static ones it owns (1, 2, 6). Checks 4–5 are live: they run `connection_cmd` and open a DB connection.
- `.claude/mysql-client.local.md` (in the *consuming* project, never in this repo) — optional per-project settings. Frontmatter has one field, `connection_cmd`, whose stdout is a `mysql://` URL; the `scripts/` converter turns it into the `[client]` INI. Skill flow lives in `SKILL.md` § Connection discovery; format/examples in `references/local-settings.md`.

## Validation

```bash
claude plugin validate .                              # marketplace
claude plugin validate plugins/mysql-client           # this plugin
cd scripts/mysql-url-to-cnf && uv sync --group dev && uv run pytest -q   # converter tests
```

CI runs all of these on push/PR via `.github/workflows/validate.yml` — it discovers the converter's `pyproject.toml` + `tests/` automatically.

## Gotchas

- **Scope guardrail.** This plugin is read-only-first. If you find yourself adding write-flavored guidance (DDL, DML, migrations, `KILL`, `SET GLOBAL`, server admin), stop — that belongs in a different plugin. The skill explicitly redirects those requests back to the user; the agent's tool allowlist refuses them at the harness level.
- **The skill `description` is load-bearing.** Claude uses it to decide whether to load the skill. Edits that drop concrete trigger phrases (e.g. "`mysql`", "`EXPLAIN`", "connection string", "login-path") narrow the trigger surface. Edits that add unrelated phrases broaden it past the plugin's competence. When changing the description, run a quick sanity check by asking a fresh Claude Code session whether the new phrasing fires on representative prompts.
- **MariaDB divergences are inline.** Every reference flags MariaDB-specific syntax where it differs (`SHOW REPLICA STATUS` vs `SHOW SLAVE STATUS`, `EXPLAIN ANALYZE` vs `ANALYZE SELECT`, no `sys` schema, no `mysql_config_editor`). Don't silently MySQL-ify; the user may be on MariaDB.
- **The subagent's safety rules are duplicated, not linked.** Subagents don't inherit the parent's skill context, so the perimeter has to live in the agent file too. If you tighten the perimeter, change it in both places (`SKILL.md` and `agents/mysql-investigator.md`).
- **`connection_cmd` stdout never enters the transcript.** The skill pipes `connection_cmd` (a URL) through the `scripts/mysql-url-to-cnf/` converter straight into a mode-600 tempfile. If you ever need to inspect the resolved connection, do it server-side (e.g. `mysql --defaults-file="$tmp" -e 'SELECT @@hostname'`) — never `cat "$tmp"`, echo the file, or print the URL.
- **The agent does not consume the settings file directly.** The skill resolves `connection_cmd` once per turn and passes the tempfile path down. Keeping resolution in one place (the skill) is the reason `agents/mysql-investigator.md`'s "Inputs" section talks about `--defaults-file=<path>` but not about `.claude/mysql-client.local.md`. If you add multi-resolution branches later, keep this discipline.
- **Version bumps touch four files:** `.claude-plugin/plugin.json`, the matching entry in the repo-root `.claude-plugin/marketplace.json`, `scripts/mysql-url-to-cnf/pyproject.toml`, and `scripts/mysql-url-to-cnf/uv.lock` (regenerated by `uv sync`). The converter project's version tracks the plugin version.
