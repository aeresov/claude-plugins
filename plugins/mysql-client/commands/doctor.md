---
description: Diagnose the mysql-client setup — client installed, settings file present and valid, connection_cmd resolves, a probe connects, .gitignore — and report what is missing. Read-only against your project and the database; runs your connection_cmd and opens one short-lived read-only connection.
allowed-tools: Bash(mysql --version), Bash(test -f *), Bash(mktemp *), Bash(rm -f *), Read, Glob
---

You are running `/mysql-client:doctor`: a health check. Write no files, dispatch no agent. Checks 1–3 and 6 are static and read-only. Checks 4–5 are **live** — they run the project's `connection_cmd` and open one short-lived read-only `mysql` connection. Those two are arbitrary project-supplied commands, so they aren't pre-approved and will each prompt for permission once — that is expected.

## Steps

1. Read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` — it defines checks 1–6 with exact commands and remediation text.

2. Run checks **1–6 in order** against the current project root:
   - 1 `mysql` client installed
   - 2 settings file present — **absent is not a failure**; connection discovery is optional
   - 3 settings file valid (SKIP if 2 absent)
   - 4 `connection_cmd` resolves — live (SKIP if 3 failed or skipped)
   - 5 connection probe — live (SKIP if 4 failed or skipped)
   - 6 `.gitignore` covers the settings file (SKIP if 2 absent)

   Use Read to parse the settings file's YAML frontmatter for checks 3–4. For checks 4–5, capture `connection_cmd` stdout into a `umask 077` `mktemp` file, use it via `mysql --defaults-file=<file>`, and `rm -f` it (plus any stderr capture file) when done. **Never** echo the tempfile's contents — it holds a password.

3. Print one line per check. When no settings file exists:
   ```
   PASS  1. mysql client installed
   n/a   2. settings file present — absent (connection discovery is optional)
   SKIP  3. settings file valid — no settings file
   SKIP  4. connection_cmd resolves — no settings file
   SKIP  5. connection probe — no settings file
   SKIP  6. .gitignore covers settings file — no settings file
   ```
   When configured (example with a failure):
   ```
   PASS  1. mysql client installed
   PASS  2. settings file present
   PASS  3. settings file valid
   FAIL  4. connection_cmd resolves
         → <remediation text from the checklist, verbatim, with the captured stderr>
   SKIP  5. connection probe — check 4 failed
   PASS  6. .gitignore covers settings file
   ```

4. End with a one-line summary:
   - All green → `OK — mysql-client ready · host <@@hostname> · <read-only|writable> · connection_cmd resolves`.
   - Settings file absent → `Connection discovery not configured (optional) — run /mysql-client:setup, or pass a connection method inline each turn`.
   - Otherwise → `<n> issue(s) — see the FAIL lines above` (if check 3 failed, append `; /mysql-client:setup can rewrite the settings file`).

Keep it terse. Don't fix anything here — point at `/mysql-client:setup` only where the checklist's remediation text already does.
