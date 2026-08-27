# Setup checklist (shared reference)

Read by `/gitlab-client:setup` and `/gitlab-client:doctor` via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`. Single source of truth for the 8 checks and their remediation text — `doctor` reports all of them, `setup` runs the static ones it owns (1, 2, 3, 8) and writes the settings files. Lives at the plugin root (not under `commands/`) so it isn't itself a slash command.

Checks 1–3 and 8 are static and read-only. Checks 4–7 are **live**: they run the user's `token_cmd` (inside `gl`) and call the GitLab API. `gl` is `${CLAUDE_PLUGIN_ROOT}/scripts/gl`; run every check from the project root. None needs `sudo`. Statuses: `PASS`, `WARN` (works, but say why), `FAIL`, `SKIP` (a prerequisite failed), `n/a` (doesn't apply).

## Checks

### 1. `python3` ≥ 3.10 on PATH

- Run: `python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'`
- PASS if exit 0.
- FAIL → "gitlab-client needs `python3` 3.10 or newer on PATH (it runs the bundled client with the system interpreter — no venv). Debian/Ubuntu: `sudo apt install python3`; macOS: `brew install python`."

### 2. User settings file present

- Run: `test -f ~/.claude/gitlab-client.local.md`
- PASS if it exists.
- **Env-only configuration:** if both `GITLAB_CLIENT_URL` and `GITLAB_CLIENT_TOKEN` are exported (`test -n "$GITLAB_CLIENT_URL" && test -n "$GITLAB_CLIENT_TOKEN"`), report checks 2 **and 3** as `n/a — configured via environment` and still run checks 4–7 (they only need `gl`, which reads the env).
- FAIL → "No `~/.claude/gitlab-client.local.md`. Run `/gitlab-client:setup` — it records the GitLab URL and the command that prints your access token. (Or export `GITLAB_CLIENT_URL` and `GITLAB_CLIENT_TOKEN`.)"

### 3. Settings valid (only if 2 passed; `n/a` when env-only)

- **Never `Read` or `cat` the settings files** — a hand-written `token_cmd` may contain the token itself, and `Read` output lands in the transcript. Use silent greps over the **frontmatter only** (`gl` ignores the Markdown body, so free-form notes there must not fail the check). For each file that exists (`~/.claude/gitlab-client.local.md`, then `.claude/gitlab-client.local.md`):
  - `sed -n '2,/^---$/p' FILE | grep -qE '^url: *["'"'"']?https?://'` → url present
  - `sed -n '2,/^---$/p' FILE | grep -qE '^token_cmd: *[^ ]'` → token_cmd present
  - `sed -n '2,/^---$/p' FILE | grep -oE '^[A-Za-z_]+:' | sort -u` → the key names in use (prints only names, never values)
- PASS if `url` and `token_cmd` are each present in at least one file and every key name is one of `url:`, `token_cmd:`, `project:`.
- FAIL → "`~/.claude/gitlab-client.local.md` needs `url: https://…` and `token_cmd: <command printing the token>` in its frontmatter. Re-run `/gitlab-client:setup`, or see `skills/gitlab-client/references/local-settings.md`." (For an unknown key: "unknown key `<k>` in a gitlab-client settings file — only `url`, `token_cmd`, `project` are read.")

### 4. `token_cmd` resolves (live; only if 3 passed or is `n/a`)

- Run: `"${CLAUDE_PLUGIN_ROOT}/scripts/gl" version; echo "exit=$?"`
- PASS if the exit code is **not 2** (an exit of 0 or 1 means the token was obtained — 1 is a network/HTTP problem for check 5). Keep the stdout/stderr for check 5.
- **Never print the token.** `gl` never prints it either; its error text shows only `token_cmd`'s stderr and exit code.
- FAIL (exit 2) → show `gl`'s stderr line, then: "`token_cmd` did not produce a token. Common causes: the secret-store CLI isn't logged in, the secret path is wrong, or the command prints more than one line. Fix `token_cmd` in `~/.claude/gitlab-client.local.md` or re-run `/gitlab-client:setup`."

### 5. Instance reachable, version (live; only if 4 passed)

- Uses the same `gl version` run. PASS if exit 0; note `version` and edition for the summary (`enterprise: true` → EE; `false`/`null` → CE — the field only exists from 15.6, and a version string without `-ee` is CE).
- WARN (still PASS for the summary count) if `gl` printed `references are written for GitLab 15.x; this instance is …` → repeat that line. A `gl: note: … predates some 15.x endpoints` line is informational — repeat it, no WARN.
- FAIL (exit 1) → show `gl`'s stderr, then: "Could not reach `<url>` as an authenticated user. 401 means the token was rejected (expired, revoked, or the wrong secret); a connection error means the host isn't reachable from here (VPN? DNS? proxy?); 404 on `/api/v4/metadata` usually means `url` points at the wrong place (relative-URL installs need the `/gitlab` prefix)."

### 6. Token scopes and expiry (live; only if 5 passed)

- Run: `"${CLAUDE_PLUGIN_ROOT}/scripts/gl" api GET /personal_access_tokens/self --fields name,scopes,expires_at,active,revoked`
- PASS if `active` is true, `revoked` is false, and `scopes` contains `api` or `read_api`.
- FAIL if `scopes` has neither `api` nor `read_api` → "The token can't use the REST API (scopes: `<list>`). Create a token with `read_api` (reads) or `api` (reads + writes) and update the secret behind `token_cmd`." (`read_repository` alone only unlocks the files endpoints.)
- WARN if `scopes` has `read_api` but not `api` → "Token is read-only (`read_api`): browsing, logs, and diffs work; creating MRs, commenting, retrying jobs and triggering pipelines will fail with 403. Create a token with the `api` scope if you want writes."
- WARN if `expires_at` is within 14 days → "Token expires on `<date>` — rotate it soon and update `token_cmd`'s secret."
- WARN if the call itself 401s/403s/404s (check 5 already proved the token works) → "Could not introspect the token (`/personal_access_tokens/self` exists from GitLab 15.5 — it returns **401** on 15.2 — and may need `read_api`). Check the token's scopes in GitLab → User settings → Access tokens: `read_api` for reads, `api` for writes."
- FAIL if `active` is false or `revoked` is true → "The token is revoked or inactive. Create a new one and update the secret behind `token_cmd`."

### 7. Project resolves (live; only if 5 passed)

- Run: `"${CLAUDE_PLUGIN_ROOT}/scripts/gl" project; echo "exit=$?"`
- PASS if exit 0; note `path_with_namespace` and `resolved_from`.
- Exit 2 — map `gl`'s message:
  - "…`git remote get-url origin` gave nothing" → `n/a` — "not in a GitLab clone; pass `--project group/name` when calling `gl`, or set `project:` in `.claude/gitlab-client.local.md`".
  - "…doesn't look like a GitLab project path" → `n/a` — "the `origin` remote isn't a GitLab project URL (not a GitLab clone); pass `--project group/name` when calling `gl`, or set `project:` in `.claude/gitlab-client.local.md`" — a non-GitLab checkout is not a setup problem.
  - "…not found or not visible" → FAIL → "The remote's path doesn't match a project this token can see. Either the project path differs from the git remote (set `project: group/name` in `.claude/gitlab-client.local.md` — `/gitlab-client:setup` can write it) or the token's user isn't a member."
- Exit 1 → FAIL with `gl`'s stderr (an API error other than 404; see check 5's causes).

### 8. `.gitignore` covers the project settings file (only if `.claude/gitlab-client.local.md` exists)

- Check the project's `.gitignore` for `.claude/*.local.md` (or an exact `.claude/gitlab-client.local.md` entry).
- PASS if covered; `n/a` if there is no project-level file.
- FAIL → "Add `.claude/*.local.md` to `.gitignore` — the settings file can name secret-store paths and shouldn't be committed."
