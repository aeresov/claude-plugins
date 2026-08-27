# gitlab-client

A Claude Code plugin that makes Claude an effective, safe user of a self-managed GitLab 15.x — repositories, merge requests, and CI — through `gl`, a small bundled REST v4 client. Reads are free; a short allow-list of writes (MRs, comments, retry/cancel/play/trigger) is permitted with guardrails; merging, approving, deleting, and settings changes are refused outright, by code, not by convention.

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install gitlab-client@aeresov-claude-plugins
/gitlab-client:setup      # URL + token command → ~/.claude/gitlab-client.local.md
/gitlab-client:doctor     # verify token, connection, project
```

Then ask Claude about an MR, a pipeline, or a file in a GitLab project — "what's failing on my branch", "show me the diff for MR 142", "reply to that review thread".

## Prerequisites

- `python3` ≥ 3.10 on `PATH`. `gl` is pure stdlib — no venv, no pip installs.
- A GitLab personal access token: `read_api` covers every read (including job logs and artifacts); `api` is required for writes. `read_repository` alone is **not** enough — it covers git-over-HTTP, not the REST API.
- Nothing else.

## Settings

Two Markdown-with-frontmatter files, three keys total. Never shown to Claude — `gl` reads them itself so the token never enters the transcript.

| Key | Where | Meaning |
|---|---|---|
| `url` | user file; project may override | Instance base URL, e.g. `https://gitlab.example.com` |
| `token_cmd` | user file; project may override | Shell command whose stdout is the PAT — recorded verbatim |
| `project` | project file only | `group/subgroup/name` — pins the project when the git remote doesn't map |

- **User:** `~/.claude/gitlab-client.local.md` — written by `/gitlab-client:setup`; required.
- **Project:** `<repo>/.claude/gitlab-client.local.md` — optional. Must be gitignored; `setup` adds `.claude/*.local.md` to `.gitignore` when it writes one.

Minimum user file:

~~~markdown
---
url: https://gitlab.example.com
token_cmd: ksm secret notation keeper://abc123/field/password
---
~~~

`token_cmd` is run through the shell, so pipes and `$(…)` work: Keeper `ksm secret notation keeper://<uid>/field/password`, pass `pass show work/gitlab-pat`, 1Password `op read "op://Work/GitLab PAT/credential"`, env var `printf '%s' "$GITLAB_TOKEN"`. It must exit 0 and print exactly one non-empty line; on failure `gl` exits 2 showing only the exit code and stderr — never stdout. **Never put a literal token in the file** (`token_cmd: echo glpat-…`) — the command line itself may be displayed by `setup`/`doctor`.

Precedence, highest first: CLI flags (`--url`, `--project`) → environment (`GITLAB_CLIENT_URL`, `GITLAB_CLIENT_TOKEN` — when the token env var is set, `token_cmd` is not run) → project file → user file.

## What Claude will and won't do

`gl` enforces a POST/PUT allow-list in code (`DELETE` isn't even parsed); anything off-list exits 3 before touching the network. On top of that, the skill splits the allowed writes by blast radius:

| Class | Operations | Behaviour |
|---|---|---|
| **Refused** | merge, approve/unapprove, rebase, delete anything, erase job logs, repository writes (branches/tags/files — git does those locally), project/group/member/variable/hook/protected-branch settings, users, tokens, `sudo` | Claude names the operation and hands it back to you — no curl workarounds. |
| **Confirmed first** | create MR, update MR (title/description/labels/reviewers/close/reopen), post a note, start a discussion, reply to a thread, resolve a thread | Claude shows the target, the exact text, and the command; sends only after an explicit yes. |
| **Go and report** | retry/cancel a job or pipeline, play a manual job, trigger a pipeline, keep artifacts | Runs it, then reports the new id, status, and `web_url`. |

## The `gl` CLI

Lives at `scripts/gl` inside the installed plugin directory; Claude resolves it via `${CLAUDE_PLUGIN_ROOT}` and drives it
via Bash. It's a perfectly usable CLI on its own too — for the snippets below, `alias gl='<plugin root>/scripts/gl'`:

```bash
gl api GET|POST|PUT PATH [k=v | k:=json | k[]=v ...] [--all] [--max N] [--fields a,b.c] [--out FILE] [--json BODY]
gl project                      # {id, path_with_namespace, default_branch, web_url, resolved_from: flag|settings|remote:<name>}
gl log JOB_ID [--tail N|--head N|--grep RE [-C N]|--sections|--section NAME] [--raw] [--refresh]
gl diff MR_IID [--files] [--file PATH] · gl diff --commit SHA · gl diff --range A..B [--straight]
gl artifacts JOB_ID [--list|--extract DIR|--file PATH [--out FILE]] · gl artifacts --ref REF --job NAME …
gl version                      # GET /metadata; warns when the instance isn't 15.x
```

Every subcommand takes `--url`, `--project`, `--remote`, `-q/--quiet`. Exit codes: **0** ok · **1** HTTP/network · **2** config/usage · **3** refused by the write policy.

- **`:project`** in an `api` path expands to the URL-encoded resolved project (from `--project`, then `project:` in settings, then the `origin` remote), so `gl api GET /projects/:project/merge_requests` works from any clone.
- **`--fields a,b.c`** projects each object to the listed dotted paths (`.N` indexes a list: `notes.0.body`) — a bare MR is ~2 KB of JSON, so lists without `--fields` waste context fast.

## Pipeline triage

"Why is CI red?" dispatches the bundled [`pipeline-debugger`](agents/pipeline-debugger.md) subagent: a read-only investigator (`Bash`, `Read`, `Grep`, `Glob` — no write tools at the harness level) that walks the failed pipeline including child pipelines, reads the failing jobs' logs section by section, correlates with the MR diff when there is one, and returns a compact report — the decisive log excerpt, the probable cause, a suggested next step — instead of pages of logs.

## Troubleshooting

- **404 on something that exists.** GitLab returns 404 both for *missing* and *not visible to this token* — also check for a path typo, a route that doesn't exist on your 15.x minor (see [`v15-compat.md`](skills/gitlab-client/references/v15-compat.md)), or a reverse proxy decoding the `%2F` in encoded paths.
- **403.** `insufficient_scope` means the token: writes need `api`, `read_api` is read-only — fix the token, not the call. Any other 403 is your *role* (below Developer for retry/cancel/play) or a protected branch/environment.
- **Huge job logs.** Never dump a log you haven't sized: `gl log JOB_ID --sections` first, then `--section NAME`, `--grep`, or `--tail`.
- **Artifact and trace downloads 302** to pre-signed object-storage URLs; `gl` strips the token before following the redirect. Replicating the download with plain curl would re-send the token to the storage host — don't.
- **"references are written for GitLab 15.x" warning** from `gl version`: the plugin still works, but the reference docs may be slightly off — expect the occasional 404 on routes that moved. The quieter **"predates some 15.x endpoints" note** means you're on an early 15.x (e.g. 15.2): `gl diff` uses `/changes` instead of `/diffs`, `/personal_access_tokens/self` (15.5) isn't there, and `detailed_merge_status` (15.6) is `null` — read `merge_status`.
- **Cache.** Logs and artifact archives land in `~/.cache/gitlab-client/<host>/` (respects `XDG_CACHE_HOME`). Safe to delete at any time; `gl` re-downloads on demand.

## License

AGPL-3.0-only. The whole `claude-plugins` repo is AGPL for consistency. See [LICENSE](../../LICENSE).
