# gitlab-client — design

**Date:** 2026-08-27 · **Plugin:** `plugins/gitlab-client/` · **Initial version:** 0.1.0 · **Target:** self-managed GitLab **15.11 EE** (license tier unknown), reachable over plain HTTPS, authenticated with a personal access token (PAT).

## 1. Goal

Make Claude Code an effective, safe user of an on-prem GitLab 15.11 for three jobs:

1. **Repository browsing** — read files, trees, commits, branches, tags, and compare refs on projects that aren't cloned locally.
2. **Merge requests** — list and read MRs, their diffs and discussion threads; create and update MRs (title, description, draft state, labels, reviewers/assignees, close/reopen); post comments and reply to / resolve threads.
3. **CI/CD** — pipeline status for a branch or MR, jobs and stages (including child pipelines), read job logs safely even when they're megabytes, retry/cancel jobs and pipelines, play manual jobs, trigger pipelines with variables, download artifacts and test reports.

Out of scope for this plugin: issues, epics, wiki; approving, merging, rebasing; deleting anything; project/group/CI-variable settings; any GitLab version other than the 15.x series (the client is version-agnostic, but the reference docs and the `v15-compat` guidance are written for 15.11).

## 2. Architecture

No MCP server. The plugin ships a small **pure-stdlib Python CLI, `gl`**, that Claude drives via Bash, plus a skill that teaches the 15.11 endpoint cookbook and the safety rules, a context-isolated subagent for pipeline triage, and `/setup` + `/doctor`. This is the `mysql-client` shape (skill + subagent + bundled script + shared checklist), with one difference: the **client resolves settings and the token itself**, so the secret never appears on a command line, in a tempfile Claude manages, or in the transcript.

```
plugins/gitlab-client/
├── .claude-plugin/plugin.json
├── CLAUDE.md                          developer notes (layout, commands, gotchas)
├── README.md                          user-facing docs
├── setup-checklist.md                 8 checks shared by /setup and /doctor
├── commands/
│   ├── setup.md                       /gitlab-client:setup
│   └── doctor.md                      /gitlab-client:doctor
├── agents/
│   └── pipeline-debugger.md           read-only CI triage subagent
├── skills/gitlab-client/
│   ├── SKILL.md                       entry point: triggers, discovery, write policy, dispatch rule
│   └── references/
│       ├── local-settings.md          both settings files, token_cmd examples
│       ├── repo-browsing.md           tree / files / commits / compare / branches / tags / search
│       ├── merge-requests.md          list / read / diffs / discussions / create / update
│       ├── pipelines.md               pipelines / jobs / bridges / logs / retry-cancel-play / trigger / artifacts
│       ├── safety-perimeter.md        allow-list, confirmation format, error semantics
│       └── v15-compat.md              post-15.11 things to avoid; deprecated-but-present fields to prefer
└── scripts/
    ├── gl                             bash launcher: PYTHONPATH=…/src python3 -m gitlab_client "$@"
    └── gitlab-client/                 uv project — pure stdlib, Python ≥ 3.10, dev dep pytest
        ├── pyproject.toml             version tracks the plugin version (CI-enforced)
        ├── src/gitlab_client/
        │   ├── __init__.py            __version__ via importlib.metadata fallback to literal
        │   ├── __main__.py            argparse dispatch → cli.*
        │   ├── cli.py                 subcommands: api, project, log, diff, artifacts, version
        │   ├── settings.py            settings-file discovery + frontmatter parsing + token resolution
        │   ├── http.py                urllib client: auth, encoding, pagination, redirects, errors, policy
        │   ├── project.py             git remote → project path → GET /projects/:path
        │   ├── log.py                 trace download, ANSI strip, section parsing, tail/grep views
        │   ├── diff.py                /diffs, /commits/:sha/diff, /compare → unified diff text
        │   └── artifacts.py           archive download/list/extract, single-file fetch
        └── tests/                     pytest; stub transport, fixtures; no live GitLab
```

All Python and shell files start with `# SPDX-License-Identifier: AGPL-3.0-only` (repo convention). `scripts/gl` is the only executable; it `exec`s `python3` with `PYTHONPATH` set to the `src/` directory — **no venv at runtime**, same as `mysql-client`'s converter. `uv` is used only for tests.

Version bumps touch four files: `.claude-plugin/plugin.json`, the repo-root `marketplace.json` entry, `scripts/gitlab-client/pyproject.toml`, and `uv.lock` (`check-version-sync.sh` + `uv sync --locked` enforce them).

## 3. Settings

Two Markdown-with-frontmatter files, both optional beyond the user-level one. Only three keys exist.

| Key         | Where                                                | Meaning                                                                                              |
|-------------|------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| `url`       | user-level; project may override                     | Instance base URL, e.g. `https://gitlab.example.com` (relative-URL installs: `https://host/gitlab`). |
| `token_cmd` | user-level; project may override                     | Shell command whose **stdout is the PAT** (one non-empty line). Recorded verbatim.                   |
| `project`   | project-level only                                   | `group/subgroup/name` — pins the project when `origin` doesn't map, or the repo isn't a GitLab clone.|

- **User-level:** `~/.claude/gitlab-client.local.md` — written by `/gitlab-client:setup`.
- **Project-level:** `<repo>/.claude/gitlab-client.local.md` — gitignored (`.claude/*.local.md`), optional.

Frontmatter parsing is a tiny stdlib parser supporting `key: value` and the `key: |` block scalar (for multi-line `token_cmd`) — no PyYAML. Unknown keys are ignored with a stderr warning. Precedence, highest first: CLI flags (`--url`, `--project`) → environment (`GITLAB_CLIENT_URL`, `GITLAB_CLIENT_TOKEN` — for CI and the live smoke test; when the token env var is set, `token_cmd` is not run) → project file → user file.

**Token resolution.** `settings.py` runs `token_cmd` with `subprocess.run(shell=True, capture_output=True, timeout=60)` — `shell=True` is deliberate: the value is a shell command line authored by the user in their own settings file (pipes, env assignments, `$(…)` are expected), not untrusted input; the same holds for `mysql-client`'s `connection_cmd`. Success requires exit 0 and exactly one non-empty stdout line after stripping; otherwise `gl` exits 2 showing only stderr and the exit code — **never stdout**. The token lives only in the process's memory and is sent as the `PRIVATE-TOKEN` header. Each `gl` invocation runs `token_cmd` once (no on-disk caching of the secret).

**Project resolution** (`project.py`), first hit wins:

1. `--project <path>` or `--project <GitLab URL>` (a URL is parsed to its `group/…/name` path; `/-/` and anything after is dropped).
2. `project:` from the project-level settings file.
3. `git remote get-url origin` (or `--remote NAME`) parsed into a path: handles `git@host:group/name.git`, `ssh://git@host[:port]/group/name.git`, `https://host[/prefix]/group/name.git`; strips a trailing `.git`. Host is **not** checked against `url` — SSH aliases and split hostnames are common; the confirmation call below is the check.

The path is URL-encoded (`urllib.parse.quote(path, safe="")`) and confirmed with `GET /projects/:path`, which also yields `id`, `default_branch`, and `web_url`. A 404 produces: `project 'group/name' not found or not visible to this token — pass --project, or set project: in .claude/gitlab-client.local.md`. The result is cached per process only.

## 4. The `gl` client

### 4.1 Command surface

```
gl api    METHOD PATH [key=value | key:=json | key[]=value ...] [--all] [--max N] [--fields a,b.c] [--out FILE] [--json BODY]
gl project [--remote NAME]
gl log    JOB_ID [--tail N] [--head N] [--grep REGEX [-C N]] [--sections] [--section NAME] [--raw] [--refresh]
gl diff   MR_IID [--files] [--file PATH]
gl diff   --commit SHA
gl diff   --range FROM..TO [--straight]
gl artifacts JOB_ID [--list | --extract DIR | --file PATH [--out FILE]]
gl artifacts --ref REF --job NAME [--list | --extract DIR | --file PATH [--out FILE]]
gl version
```

Global flags on every subcommand: `--url`, `--project`, `--remote`, `-q/--quiet`. Exit codes: **0** ok · **1** HTTP/API error · **2** usage or configuration error (no settings, `token_cmd` failed, project unresolved) · **3** refused by the write policy.

### 4.2 `gl api`

- `METHOD` ∈ `GET | POST | PUT` (case-insensitive). `DELETE` is not accepted — there's nothing in scope that needs it, and refusing it in the parser is simpler than a deny-list.
- `PATH` is relative to `/api/v4`. The literal segment **`:project`** is replaced with the URL-encoded resolved project path (so `gl api GET /projects/:project/merge_requests` works from any clone). Any other segment that needs encoding (branch names, file paths with `/`) is the caller's job; the reference docs show `--file-path` style examples with `%2F`, and `gl diff`/`gl artifacts` do it internally.
- **Params:** `key=value` (string), `key:=<json>` (typed — numbers, booleans, arrays, objects), `key[]=value` (repeatable → array). GET → query string (`urlencode(doseq=True)`, arrays as `key[]=`). POST/PUT → one JSON body, `Content-Type: application/json`. `--json BODY` sends a raw JSON body instead.
- **`--all`:** follows pagination and prints one concatenated array. Offset endpoints: sets `per_page=100`, loops on the `x-next-page` header (falls back to `Link rel="next"`); never relies on `x-total`. For paths matching `/repository/tree$` it switches to keyset (`pagination=keyset&per_page=100`, following `Link rel="next"`) because 15.x no longer supports offset paging there. `--max N` caps the item count (default 1000) and warns on stderr when the cap truncates.
- **`--fields a,b.c`:** projects each object (or each element of an array) to the listed dotted paths; an all-digit segment indexes a list (`notes.0.body`); missing → `null`. Exists purely for context economy — a bare MR is ~2 KB of JSON.
- **`--out FILE`:** streams the raw response body to `FILE` (binary, 1 MiB chunks) and prints one line `wrote <bytes> bytes to <FILE>`; used for traces, artifacts, raw files.
- Output is pretty JSON (indent 2) on stdout; non-JSON responses (raw files, `/trace`) are written verbatim unless `--out` is given.

### 4.3 HTTP behaviour (`http.py`)

- Auth: `PRIVATE-TOKEN: <pat>` header. `User-Agent: gitlab-client/<version> (claude-code plugin)`. `Accept: application/json` unless the path is a known raw/binary route.
- Timeouts: 30 s for JSON calls, 300 s for `--out` downloads.
- **Redirects:** custom `HTTPRedirectHandler`; on a redirect whose target host differs from the API host, `PRIVATE-TOKEN` (and any `Authorization`) is dropped before following. GitLab 302s artifact and trace downloads to pre-signed object-storage URLs; urllib would otherwise re-send the token to the storage host.
- **429:** honour `Retry-After` (cap 30 s), retry once, then fail. The body may be plain text — never assume JSON on errors.
- **Errors:** one line on stderr, exit 1: `HTTP <status> <method> <url>: <message|error|error_description>`; both `{"message": …}` and `{"error": …}` shapes are handled, and `insufficient_scope` is expanded to `token lacks the '<scope>' scope`. 404 is annotated `(GitLab returns 404 for both missing and not-visible resources)`. Network errors name the host.
- **Write policy (allow-list, enforced in code, exit 3):** POST/PUT are permitted only on these path patterns. Everything else is refused with `refused by gitlab-client write policy: <method> <path> (see safety-perimeter.md)`.

  ```
  POST /projects/:id/merge_requests
  PUT  /projects/:id/merge_requests/:iid                 # title, description, labels, reviewers, state_event close|reopen …
  POST /projects/:id/merge_requests/:iid/notes
  POST /projects/:id/merge_requests/:iid/discussions
  POST /projects/:id/merge_requests/:iid/discussions/:did/notes
  PUT  /projects/:id/merge_requests/:iid/discussions/:did  # resolved=true|false
  POST /projects/:id/pipeline                            # trigger (note: singular)
  POST /projects/:id/pipelines/:pid/retry
  POST /projects/:id/pipelines/:pid/cancel
  POST /projects/:id/jobs/:jid/retry
  POST /projects/:id/jobs/:jid/cancel
  POST /projects/:id/jobs/:jid/play
  POST /projects/:id/jobs/:jid/artifacts/keep
  ```

  Explicitly outside the list (and therefore refused): `…/merge`, `…/approve`, `…/unapprove`, `…/approvals`, `…/rebase`, `…/jobs/:id/erase`, `…/repository/*` writes (branches, tags, files, commits — git does those locally), project/group/member/variable/hook/protected-branch settings, `/users`, `/personal_access_tokens`, anything under `/admin`, and the `sudo` parameter (refused in code on every verb, including in `--json` bodies). The policy check runs on the raw path *before* project resolution, so a refused call never runs `token_cmd` or touches the network; it also refuses, on every verb, paths that wouldn't round-trip through urllib (`#`, whitespace, control or non-ASCII characters, `.`/`..` segments), so the path matched is exactly the path sent. There is no override flag; if the user wants more, that's a plugin change.

### 4.4 `gl log`

Downloads `GET /projects/:id/jobs/:job_id/trace` to a cache file `$XDG_CACHE_HOME/gitlab-client/<host>/job-<job_id>.log` (default `~/.cache`). Fetches `GET /jobs/:job_id` first for the header line and to decide caching: terminal statuses (`success`, `failed`, `canceled`, `skipped`) are cached; anything else, or `--refresh`, re-downloads.

Output always starts with one header line: `job <id> <name> · stage <stage> · <status> · <duration>s · <bytes> bytes · <web_url>`. Then:

- default: last **150** lines (ANSI-stripped, `\r` runs collapsed).
- `--tail N` / `--head N` / `--grep REGEX [-C N]` (case-insensitive by default, prints `<line-no>: <text>`).
- `--sections`: one line per `section_start:<ts>:<name>` … `section_end` pair — `<name> · <lines> lines · <duration>s`; unterminated sections are marked. `--section NAME` prints that section's body (still subject to `--tail`/`--head` if given).
- `--raw`: skip ANSI stripping (still tailed).

No heuristic "find the failing part"; that's the subagent's job, using `--sections` and `--grep`. The full log is never printed without an explicit `--tail`/`--head`/`--section` bound; an unbounded request (`--tail 0` or `--head 0`) is allowed but prints a stderr warning with the byte count first.

### 4.5 `gl diff`

Renders GitLab's per-file diff JSON as a unified diff so Claude reads it like `git diff`:

```
diff --git a/<old_path> b/<new_path>
new file mode <b_mode>          # when new_file
deleted file mode <a_mode>      # when deleted_file
rename from … / rename to …     # when renamed_file
--- a/<old_path>  (or /dev/null)
+++ b/<new_path>  (or /dev/null)
<diff field verbatim — GitLab already supplies the @@ hunks>
```

- `gl diff MR_IID` → `GET /merge_requests/:iid/diffs --all`. `--files` prints `<status> <path>` per file (A/M/D/R) plus a count. `--file PATH` prints just that file.
- `--commit SHA` → `GET /repository/commits/:sha/diff`; `--range FROM..TO [--straight]` → `GET /repository/compare?from&to&straight` (renders `commits[]` as a one-line-per-commit preamble, then `diffs[]`).
- A file whose `diff` is an empty string but is neither new nor deleted is rendered as `[diff omitted by server — exceeds the instance's diff limits]`; 15.11 has no `collapsed`/`too_large` flags, so the empty string is the only signal.

### 4.6 `gl artifacts`

- `gl artifacts JOB_ID` downloads `GET /jobs/:id/artifacts` to `$XDG_CACHE_HOME/gitlab-client/<host>/job-<id>-artifacts.zip` (auth-safe redirect handling) and prints its path and size.
- `--list` prints the zip's entries (`<size> <path>`) via `zipfile` — 15.11 has no `/artifacts/tree` endpoint. `--extract DIR` extracts (refusing entries that escape `DIR`). `--file PATH` fetches one file through `GET /jobs/:id/artifacts/<path>` to stdout (or `--out FILE`) without downloading the archive.
- `--ref REF --job NAME` uses the by-ref routes (`/jobs/artifacts/:ref/download?job=` and `/jobs/artifacts/:ref/raw/<path>?job=`). Error text on 404 notes the 15.11 limitation: only the **single latest successful** pipeline on that ref is considered, and `ref` must be a branch or tag (not a SHA).

### 4.7 `gl project` and `gl version`

`gl project` prints `{id, path_with_namespace, default_branch, web_url, resolved_from: "flag|settings|remote:<name>"}`. `gl version` prints `GET /metadata` (`version`, `revision`, `enterprise`) and warns on stderr when the major version isn't 15: `references are written for GitLab 15.11; this instance is <version>`.

## 5. Skill `gitlab-client`

`SKILL.md` (≈ the size of `mysql-client`'s) owns:

- **Description / triggers.** Fires on: merge request / MR, pipeline, job, CI failure, job log, artifact, "what's failing on my branch/MR", a pasted GitLab URL, a `group/project` reference, "open an MR for this branch", "reply to the review thread", "retry the failed job", "show me file X in project Y", "what changed between tags". Names GitLab explicitly so it doesn't fire for GitHub.
- **Discovery.** Run `gl version` the first time GitLab is needed in a turn. Exit 2 → point at `/gitlab-client:setup` (settings missing) or show the stderr (token_cmd failed). Never try to read or print the token. Locate `gl` as `${CLAUDE_PLUGIN_ROOT}/scripts/gl` (in the skill body the path is given relative to the skill directory, resolved to absolute before use — same wording as `mysql-client`).
- **Write policy (user-facing layer over the code allow-list).**
  - *Refused outright* — anything not on the allow-list; hand it back to the user by name (merge, approve, delete, rebase, settings, repository writes → "use git locally").
  - *Confirm first* — creating or updating an MR, posting any note/discussion/reply, resolving a thread: show the exact target (`project!iid`) and the payload (title/description/body verbatim), ask, and send only after an explicit yes. A "just do it" for the turn still needs one confirmation per distinct payload.
  - *Go and report* — retry/cancel jobs and pipelines, play manual jobs, trigger a pipeline (with the variables echoed), keep artifacts. Report the resulting job/pipeline id, status, and `web_url`.
- **Context economy.** Lists always with `--fields`; `--all` only with a filter (`state=opened`, `ref=`, `updated_after=`); `gl log` never with `--tail 0`; big diffs via `--files` first, then per file; raw files of unknown size via the JSON files endpoint's `--fields size,encoding` first (`gl` has no `HEAD` verb), then `/raw --out`.
- **Inline vs dispatch.** One lookup, one write, or a small read → inline. Failed-pipeline triage, "why is CI red", anything needing 5+ calls or reading logs from more than one job → dispatch `pipeline-debugger` with the project path, the identifiers, and the question.
- **Stepping out.** Issues/epics, approvals/merge, other forges, `python-gitlab`/`glab` code questions.

The six reference files are each self-contained (purpose → command → gotcha), built from Appendix A. `v15-compat.md` is the short "don't" list: no `unidiff`, `inputs`, `job_inputs`, `/trigger_jobs`, `/artifacts/tree`, `search_recent_successful_pipelines`, `draft=` filter (use `wip=`), `name` pipeline filter; prefer `detailed_merge_status` over `merge_status`, `merge_user` over `merged_by`, `/diffs` over `/changes`, `head_pipeline` over `pipeline`.

## 6. Subagent `pipeline-debugger`

- **Frontmatter:** `tools: Bash, Read, Grep, Glob` (no `Edit`/`Write`/`NotebookEdit`/`Agent`). `model: sonnet` (repo convention — the other plugins' subagents pin it too).
- **Inputs (from the calling skill):** the `gl` path, the project path, one of {pipeline id, job id, branch, MR iid}, and the question. It does **not** read settings files or resolve tokens — `gl` does that on every call, so the agent needs nothing secret.
- **Flow:** resolve the pipeline (`/pipelines/:id`, or `/merge_requests/:iid` → `head_pipeline`, or `/pipelines?ref=&order_by=updated_at&per_page=1`) → `/pipelines/:id/jobs?include_retried=false --fields id,name,stage,status,allow_failure,web_url` → `/pipelines/:id/bridges` and recurse into `downstream_pipeline.id` → for each failed job: `gl log <id> --sections`, then the last section(s) and `--grep -i 'error|fail|exception|traceback|fatal' -C 3` → if an MR is in play, `gl diff <iid> --files` to correlate touched paths with the failing job.
- **Output contract:** ≤ 40 lines per failing job: job name/stage/id/`web_url`, the decisive log excerpt (≤ 20 lines, verbatim), probable cause, and a suggested next step (which `gl` write to run, or which file to change). Never runs retry/cancel/play itself — it recommends.
- **Safety perimeter duplicated verbatim** from the skill (subagents don't inherit skill context); tool list enforces read-only at the harness level anyway.

## 7. Commands and the shared checklist

`setup-checklist.md` at the plugin root (not under `commands/`) defines eight checks with exact commands and remediation text; `doctor` reports all, `setup` runs the static ones it owns (1, 2, 3, 8).

| # | Check                                   | How                                                                                                     | Live? |
|---|-----------------------------------------|---------------------------------------------------------------------------------------------------------|-------|
| 1 | `python3` ≥ 3.10 on PATH                | `python3 -c 'import sys; sys.exit(sys.version_info < (3,10))'`                                          | no    |
| 2 | user settings file present              | `test -f ~/.claude/gitlab-client.local.md` — absent → FAIL (unlike mysql-client, there is no inline fallback) | no |
| 3 | settings valid                          | `url` is `http(s)://…`, `token_cmd` non-empty (project file, if present, only has known keys)          | no    |
| 4 | `token_cmd` resolves                    | `gl version` exit code ≠ 2; stdout of `token_cmd` is never shown, its stderr is                         | yes   |
| 5 | instance reachable, version             | `gl version` → PASS with `<version> (EE)`; WARN (not FAIL) if major ≠ 15                                | yes   |
| 6 | token scopes & expiry                   | `gl api GET /personal_access_tokens/self --fields scopes,expires_at,active`; FAIL if no `read_api`/`api`; WARN if no `api` (writes will 403) or expiry < 14 days | yes |
| 7 | project resolves                        | `gl project` in the current repo; `n/a` outside a git repo or with no GitLab-looking remote and no `project:` | yes |
| 8 | `.gitignore` covers `.claude/*.local.md`| only when a project-level file exists                                                                    | no    |

- **`/gitlab-client:setup`** — interactive. Check 1 first (stop on fail). If the user file exists: show `url` and whether `token_cmd` is set (never its value), offer Keep / Reconfigure / Abort. Ask for the URL; ask where the token comes from (Keeper `ksm`, `pass`, 1Password `op`, env var → `printf '%s' "$VAR"`, other) and assemble `token_cmd` verbatim. A literal token in the file is refused (setup and doctor may show the `token_cmd` line — never the token). Neither command ever `Read`s the settings files; validity checks are silent `grep -q`s. Write `~/.claude/gitlab-client.local.md`. Then offer a project-level file: only if `origin` doesn't parse or the user wants to pin `project:`; write it and fix `.gitignore` (check 8). Never runs `token_cmd`; prints "run `/gitlab-client:doctor` to verify".
- **`/gitlab-client:doctor`** — read-only; runs 1–8, prints one `PASS/WARN/FAIL/SKIP/n-a` line per check with the checklist's remediation text verbatim, ends with `OK — gitlab-client ready · <host> · GitLab <version> · scopes <…> · project <path>` or `<n> issue(s) — see FAIL lines above`. Checks 4–7 run `token_cmd` and hit the API, so they prompt for permission once each — expected.

Both commands' `allowed-tools` are limited to the listed shell probes, `Read`, `Glob`, `Write`/`Edit` (setup only) and `AskUserQuestion` (setup only).

## 8. Error handling summary

| Situation                              | Behaviour                                                                                   |
|----------------------------------------|---------------------------------------------------------------------------------------------|
| no settings / bad frontmatter          | exit 2, message names the file and the missing key, mentions `/gitlab-client:setup`         |
| `token_cmd` fails / empty / multi-line | exit 2, shows the command's stderr and exit code only                                       |
| project unresolved                     | exit 2, shows what was tried (flag / settings / remote URL) and the two fixes               |
| HTTP 401                               | exit 1, `token rejected — check token_cmd output and the token's expiry (/doctor)`          |
| HTTP 403 `insufficient_scope`          | exit 1, names the missing scope                                                             |
| HTTP 403 other                         | exit 1, adds `(role too low? Developer is needed to retry/cancel/play; protected branch?)`  |
| HTTP 404                               | exit 1, notes the ambiguity (missing vs not visible)                                        |
| HTTP 429                               | one retry after `Retry-After` (≤ 30 s), then exit 1                                         |
| write policy                           | exit 3 before any network call                                                              |
| network / TLS / timeout                | exit 1, names host and the exception class                                                  |

## 9. Testing

`scripts/gitlab-client/tests/`, pytest, run by CI via the existing auto-discovery (`uv sync --locked --group dev && uv run pytest -q`). No live GitLab; `http.py` takes an injectable opener so tests use a **stub transport** that records requests and replays canned responses/headers.

- `settings`: frontmatter parser (plain, block scalar, unknown key warning, missing file); precedence flag > env > project > user; `token_cmd` success/failure/multi-line/timeout; token never in error output.
- `project`: remote-URL table (`git@`, `ssh://` with port, `https://` with path prefix, subgroups, `.git`, no `.git`, GitLab URL with `/-/merge_requests/…`); 404 message.
- `http`: header set; `:project` substitution; param typing (`=`, `:=`, `[]`); GET query vs POST JSON body; offset pagination loop stopping on empty `x-next-page`; keyset switch for `/repository/tree`; `--max` cap warning; `--fields` projection; redirect same-host keeps token, cross-host drops it; 429 plain-text retry then fail; both error shapes; `insufficient_scope`; write allow-list (every allowed pattern passes, a table of refused paths exits 3, `DELETE` is a parse error).
- `log`: ANSI stripping and `\r` handling on a captured runner trace fixture with nested/unterminated sections; `--sections` durations; `--section`, `--tail`, `--head`, `--grep -C`; cache hit for terminal status, refetch for `running`/`--refresh`.
- `diff`: MR/commit/compare rendering incl. new/deleted/renamed modes and the empty-diff marker; `--files` letters and count; `--file` selection.
- `artifacts`: `--list`/`--extract` on a fixture zip (including a path-traversal entry that must be refused); `--file` route encoding; by-ref 404 message.
- `cli`: exit codes; `gl version` warning on a non-15 major.
- **Live smoke test** (skipped unless `GITLAB_CLIENT_LIVE=1` and `GITLAB_CLIENT_URL`/`GITLAB_CLIENT_TOKEN` are set): `gl version` and `GET /user` only.

Manual verification against the real 15.11 instance during implementation (Appendix B) is recorded in the plugin `CLAUDE.md` once done.

## 10. Documentation deliverables

- `README.md` — quick start (`/plugin install gitlab-client@aeresov-claude-plugins`, `/gitlab-client:setup`, `/gitlab-client:doctor`), prerequisites (`python3` ≥ 3.10, a PAT with `read_api` or `api`), the settings files, what Claude will and won't do (write policy), the `gl` cheat-sheet, troubleshooting (404 ambiguity, 403 role vs scope, huge logs, object-storage redirects).
- `CLAUDE.md` — layout, test commands, gotchas (token never on the command line; allow-list is code + doc — change both; keyset for tree; redirect auth-stripping; references target 15.11; version bumps touch four files).
- Repo-root `CLAUDE.md` gains the plugin bullet; `marketplace.json` gains the entry (`version` 0.1.0, AGPL-3.0-only, homepage/repository like the others).

## Appendix A — GitLab 15.11 endpoint inventory (verified against archived docs)

Base: `https://<host>/api/v4`. `:id` = numeric project id **or** URL-encoded path (`group%2Fsub%2Fname`). MRs are addressed by project-scoped **iid**. Reads need `read_api`; writes need `api`. Offset pagination (`page`, `per_page` ≤ 100; loop on `x-next-page`) unless noted.

### A.1 Instance / identity
| Purpose | Call | Notes |
|---|---|---|
| version probe | `GET /metadata` | 15.2+; `version`, `revision`, `enterprise` (15.6+). `/version` still exists but docs say prefer `/metadata`. |
| who am I | `GET /user` | `id`, `username`, `name`. |
| token introspection | `GET /personal_access_tokens/self` | 15.5+; `scopes[]`, `expires_at`, `active`, `revoked`. Never call `DELETE …/self`. |

### A.2 Repository
| Purpose | Call | Notes |
|---|---|---|
| project lookup | `GET /projects/:id` | `id`, `path_with_namespace`, `default_branch`, `web_url`, `ssh_url_to_repo`, `http_url_to_repo`, `permissions`. |
| tree | `GET /projects/:id/repository/tree?path=&ref=&recursive=` | **keyset only** since 15.0: `pagination=keyset&per_page=100`, follow `Link rel="next"`. Missing path → `200 []` on 15.11. |
| file (JSON) | `GET /projects/:id/repository/files/:file_path?ref=` | `file_path` fully encoded (`%2F`); **`ref` required**; `content` base64, `size`, `blob_id`. `HEAD` returns `X-Gitlab-Size` etc. |
| file (raw) | `GET /projects/:id/repository/files/:file_path/raw?ref=` | bytes; `HEAD` first for size; `lfs=true` for LFS content. |
| blob by sha | `GET /projects/:id/repository/blobs/:sha[/raw]` | avoids path encoding once `blob_id` is known. |
| blame | `GET /projects/:id/repository/files/:file_path/blame?ref=&range[start]=&range[end]=` | range params encoded `range%5Bstart%5D`; docs contradictory on whether required — always pass. |
| commits | `GET /projects/:id/repository/commits?ref_name=&path=&since=&until=&author=` | `ref_name` accepts `A..B`; `author` 15.10+; don't rely on `x-total`. |
| commit | `GET /projects/:id/repository/commits/:sha` | accepts branch/tag name; includes `last_pipeline`. |
| commit diff | `GET /projects/:id/repository/commits/:sha/diff` | no `unidiff` on 15.11. |
| commit refs / MRs | `GET …/commits/:sha/refs`, `GET …/commits/:sha/merge_requests` | |
| commit statuses | `GET …/commits/:sha/statuses?ref=&all=` | |
| compare | `GET /projects/:id/repository/compare?from=&to=&straight=` | unpaginated; `commits[]`, `diffs[]`, `compare_timeout`. |
| merge base | `GET /projects/:id/repository/merge_base?refs[]=&refs[]=` | |
| branches / tags | `GET …/repository/branches[/:name]`, `GET …/repository/tags[/:name]` | `/`-containing names `%2F`-encoded in path segments; tags `order_by=version` 15.4+. |
| archive | `GET /projects/:id/repository/archive.zip?sha=&path=` | 5 req/min/project. |
| code search | `GET /projects/:id/search?scope=blobs&search=` | **Premium + Elasticsearch** on 15.11 docs; feature-detect (400/403/empty) once per process and fall back to `tree` + `raw` + local grep. |
| members (reviewer lookup) | `GET /projects/:id/members/all?query=` | preferred over `/users` — only assignable people; `id`, `username`, `name`, `access_level`. |
| labels | `GET /projects/:id/labels?search=` | validate label names before `add_labels`. |

### A.3 Merge requests
| Purpose | Call | Notes |
|---|---|---|
| list | `GET /projects/:id/merge_requests?state=opened&source_branch=&target_branch=&author_username=&reviewer_username=&search=&wip=yes\|no&order_by=updated_at` | no `draft=` filter (19.0); `approved_by_ids`/`approver_ids` are Premium; `with_merge_status_recheck` may be ignored below Developer. |
| read | `GET /projects/:id/merge_requests/:iid` | use `detailed_merge_status` (15.6+), `draft`, `head_pipeline` (full object), `diff_refs`, `changes_count` (string, `"1000+"` cap). `diff_refs` is empty right after creation — retry. |
| diffs | `GET …/merge_requests/:iid/diffs` (paginated) | per-file `old_path`, `new_path`, `a_mode`, `b_mode`, `diff`, `new_file`, `renamed_file`, `deleted_file`; empty `diff` = over limits. `/changes` is deprecated (15.7) — only use for `overflow`/`access_raw_diffs`. |
| commits / pipelines / versions | `GET …/:iid/commits`, `GET …/:iid/pipelines`, `GET …/:iid/versions` | `/pipelines` returns minimal `{id, sha, ref, status}` — prefer `head_pipeline`. |
| discussions | `GET …/:iid/discussions` | threads with `notes[]`, `resolvable`, `resolved`, `position` for diff notes; `individual_note=true` = plain comment. |
| new thread / comment | `POST …/:iid/discussions {body, position?}` | diff note needs `position[base_sha/start_sha/head_sha/old_path/new_path/new_line\|old_line, position_type=text]` from `diff_refs`. |
| reply | `POST …/:iid/discussions/:discussion_id/notes {body}` | `discussion_id` is a 40-hex string. |
| resolve | `PUT …/:iid/discussions/:discussion_id?resolved=true` | Developer, or author of the MR. |
| plain note | `POST …/:iid/notes {body}` | cannot reply to a thread. |
| create | `POST /projects/:id/merge_requests {source_branch, target_branch, title, description?, labels?, reviewer_ids[]?, assignee_ids[]?, remove_source_branch?, squash?, milestone_id?}` | draft = title prefix `Draft:` (no boolean; `WIP:` removed 14.8). 409 on duplicate. |
| update | `PUT /projects/:id/merge_requests/:iid {title?, description?, add_labels?, remove_labels?, reviewer_ids[]?, assignee_ids[]?, state_event=close\|reopen, target_branch?}` | `labels` **replaces**; `reviewer_ids=[]`/`0` **unsets all** — omit to keep; at least one attribute required. |

### A.4 CI/CD
| Purpose | Call | Notes |
|---|---|---|
| pipelines | `GET /projects/:id/pipelines?ref=&status=&source=&sha=&updated_after=&order_by=updated_at&sort=desc` | child pipelines not listed (17.0); `name` filter is feature-flagged — don't use. |
| pipeline | `GET /projects/:id/pipelines/:pid` | works for child ids. `GET …/pipelines/latest?ref=` also exists (docs' POST example is a typo). |
| jobs | `GET /projects/:id/pipelines/:pid/jobs?scope[]=failed&include_retried=` | `scope[]` needs bracket encoding; no stages endpoint — group by `stage`; ordered by id desc. |
| bridges | `GET /projects/:id/pipelines/:pid/bridges` | → `downstream_pipeline.id` for child/multi-project pipelines (`/trigger_jobs` is 19.2). |
| job | `GET /projects/:id/jobs/:jid` | `status` ∈ created/pending/running/failed/success/canceled/skipped/waiting_for_resource/manual. |
| log | `GET /projects/:id/jobs/:jid/trace` | raw text, ANSI + `section_start:<ts>:<name>\r\x1b[0K` markers; no Range/tail; may 302 to object storage; runner default cap 4 MB, instance cap 100 MB. |
| retry / cancel job | `POST …/jobs/:jid/retry`, `POST …/jobs/:jid/cancel` | Developer; retry creates a **new** job id; cannot retry a bridge job on 15.11; returns 201. |
| play | `POST …/jobs/:jid/play {job_variables_attributes: [{key, value}]}` | manual jobs only. |
| retry / cancel pipeline | `POST …/pipelines/:pid/retry`, `POST …/pipelines/:pid/cancel` | retry keeps the pipeline id; cancel may return 200 with nothing cancelled — re-fetch status. |
| trigger | `POST /projects/:id/pipeline {ref, variables: [{key, value, variable_type?}]}` | **singular** path; array shape (the `/trigger/pipeline` token route uses a hash — not used). |
| test report | `GET …/pipelines/:pid/test_report_summary` (14.2+), `…/test_report` | needs `artifacts:reports:junit`. |
| pipeline variables | `GET …/pipelines/:pid/variables` | |
| artifacts (archive) | `GET /projects/:id/jobs/:jid/artifacts` | zip; 302 to object storage; Reporter. |
| artifacts (one file) | `GET /projects/:id/jobs/:jid/artifacts/<path>` | path segments encoded. |
| artifacts by ref | `GET /projects/:id/jobs/artifacts/:ref/download?job=`, `…/artifacts/:ref/raw/<path>?job=` | `ref` = branch/tag only; only the latest successful pipeline is searched. No `/artifacts/tree` (18.8). |
| keep artifacts | `POST …/jobs/:jid/artifacts/keep` | role undocumented; expect Developer. |

### A.5 Mechanics
- Auth header `PRIVATE-TOKEN` (or `Authorization: Bearer`); never the `private_token` query param. `read_api` covers every read here including traces and artifacts; `api` for every write; `read_repository` covers only the files API — not enough.
- Errors: `{"message": …}` for resource/validation errors, `{"error": …}` for unknown routes, `{"error":"insufficient_scope", "scope": …}` on 403; 429 body may be plain text. 404 doubles as "not visible". POST success is 201.
- Rate limits on self-managed are **off by default**; when on, 7200 req/h/user for the authenticated API. Note creation 300/min; archive 5/min.
- Above 10,000 records `x-total`, `x-total-pages`, `rel="last"` disappear; offset cap `offset_pagination_limit` defaults to 50,000.
- Tokens created without expiry get a one-year expiry forced on upgrade to 16.0 — hence check 6.

## Appendix B — verify on the real instance during implementation

Defaults are chosen so none of these block; each is a one-call probe recorded in the plugin `CLAUDE.md` afterwards.

1. `GET /metadata` and `GET /personal_access_tokens/self` with a `read_api`-only token — both undocumented for scope. Fallback for doctor: `GET /user` if `/metadata` 401/403s.
2. `Range: bytes=-65536` on `/trace` — if 206 comes back, `gl log --tail` can skip full downloads later (not in 0.1.0).
3. `GET /projects/:id/search?scope=blobs` on this license without Elasticsearch — the response shape drives the feature-detect branch.
4. `POST …/jobs/:id/retry` on a non-retryable job and `…/play` on a non-manual job — exact status/body for the error mapping.
5. `reviewer_ids`/`assignee_ids` with more than one id on this license tier — does it 400, truncate, or accept? Default the skill to one reviewer unless verified.
6. Whether an unknown label name in `add_labels` is created or ignored — the skill validates against `/labels` first either way.
7. An artifact download that 302s to object storage — confirms the redirect handler end-to-end.
