# Setup checklist (shared reference)

Read by `/mysql-client:setup` and `/mysql-client:doctor` via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`. Single source of truth for the 6 checks and their remediation text — `doctor` reports all of them, `setup` runs the static ones it owns (1, 2, 6) and writes the settings file. Lives at the plugin root (not under `commands/`) so it isn't itself a slash command.

Checks 1–3 and 6 are static and read-only. Checks 4–5 are **live**: they run the project's `connection_cmd` and open one short-lived `mysql` connection. Every check runs from the project root; none needs `sudo`.

## Checks

### 1. `mysql` client installed

- Run: `mysql --version`
- PASS if it exits 0.
- FAIL → "Install the MySQL/MariaDB command-line client. Debian/Ubuntu: `sudo apt install mysql-client` (or `mariadb-client`, or `default-mysql-client`). RHEL/Fedora: `sudo dnf install mysql` (or `mariadb`). It provides the `mysql` binary this plugin drives."

### 2. Settings file present

- Run: `test -f .claude/mysql-client.local.md`
- PASS if it exists.
- **Absent is not a failure.** Connection discovery via a settings file is optional — the plugin also works with a connection method passed inline each turn (a login-path name, a `--defaults-file` path, a full `mysql` command prefix). When absent, report the check as `n/a` and SKIP checks 3, 4, 5, and 6. Mention `/mysql-client:setup` for users who want auto-discovery.

### 3. Settings file valid (only if 2 passed)

- Parse the YAML frontmatter; look at `connection_cmd`.
- PASS if `connection_cmd` is present and non-empty.
- FAIL → "`.claude/mysql-client.local.md` has no usable `connection_cmd`. The frontmatter needs exactly that one field — a shell command whose stdout is the body of a `[client]`-section INI. Re-run `/mysql-client:setup`, or see `skills/mysql-client/references/local-settings.md`."

### 4. `connection_cmd` resolves (live; only if 3 passed)

- Capture stdout to a mode-600 tempfile and stderr to a second tempfile:
  ```bash
  umask 077
  cnf="$(mktemp --suffix=.cnf)"
  err="$(mktemp --suffix=.err)"
  { <connection_cmd> ; } > "$cnf" 2> "$err"
  ```
- PASS if the command exits 0, `$cnf` is non-empty, and its content contains a `[client]` line.
- **Never print `$cnf`'s content** — it holds a password. `$err` is safe to surface.
- FAIL → show the captured stderr, then: "`connection_cmd` failed or produced unusable output. Common causes: an expired auth token, a wrong secret path, a `jq` filter that doesn't match the secret's JSON shape, or the secret-store CLI not being logged in. Fix the command in `.claude/mysql-client.local.md` (or re-run `/mysql-client:setup`)."
- Keep `$cnf` for check 5. Delete `$cnf` and `$err` once check 5 is done — or immediately, if check 5 is skipped.

### 5. Connection probe (live; only if 4 passed)

- Run:
  ```bash
  mysql --defaults-file="$cnf" --safe-updates -e \
    'SELECT @@hostname, @@version, @@read_only, @@super_read_only, USER(), DATABASE()'
  ```
- PASS if it connects and returns a row. Note `@@hostname`, `@@version`, and the read-only flags for the summary line.
- FAIL → show the `mysql` error, then: "Credentials resolved but the connection failed. Common causes: wrong host/port, `Access denied` (the grant doesn't cover this client host), an SSL mode/CA mismatch, or an auth-plugin mismatch (`caching_sha2_password` against an old client). See `skills/mysql-client/references/connecting.md`."
- Afterwards: `rm -f "$cnf" "$err"`.

### 6. `.gitignore` covers the settings file (only if 2 passed)

- Check the project's `.gitignore` for `.claude/*.local.md` (or an exact `.claude/mysql-client.local.md` entry).
- PASS if covered.
- FAIL → "Add `.claude/*.local.md` to `.gitignore` — the settings file names secret-store paths and shouldn't be committed."
