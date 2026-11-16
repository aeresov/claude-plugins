---
name: gitlab-client
description: Work with a self-managed GitLab (15.x) through the bundled `gl` CLI. Activate when the user mentions GitLab, a merge request / MR, a pipeline, a CI job, a job log, an artifact, "what's failing on my branch", pastes a GitLab URL, names a `group/project`, asks to open or update an MR, reply to or resolve a review thread, retry or cancel a job, trigger a pipeline, or read a file / commit / diff from a GitLab project that isn't cloned locally. Reads freely; writes are allow-listed (create/update MRs, comments, retry/cancel/play/trigger) and MR/comment writes are confirmed first; merge, approve, delete, and settings changes are refused. Not for GitHub, Bitbucket, or GitLab issues/epics.
---

# gitlab-client

Make the assistant an effective, safe user of a self-managed GitLab through `gl`, a small bundled REST v4 client. The skill owns *which* calls to make and *when to stop and ask*; `gl` owns auth, encoding, pagination, and the write allow-list. The reference docs cover the **GitLab 15.x** series (verified live on 15.2.5 CE; anything that arrived later in 15.x carries its minimum version) — `gl version` warns when the instance is outside 15.x and notes when it predates 15.11.

## Client compatibility

This skill supports Claude Code and Codex. Resolve `../..` from this SKILL.md's directory to the absolute plugin root and substitute it for `<plugin-root>` below. Keep shell calls in the consuming project directory. Both clients intentionally share the settings files — the user-level `~/.claude/gitlab-client.local.md` and the gitignored project `.claude/gitlab-client.local.md` — so no settings migration is needed. Setup and the health check are the `gitlab-client-setup` and `gitlab-client-doctor` skills (in Claude Code also `/gitlab-client:setup` and `/gitlab-client:doctor`).

In a sandboxed client, `gl` needs network access to the GitLab instance and write access to its cache (`$XDG_CACHE_HOME/gitlab-client`, default `~/.cache/gitlab-client` — `gl log` and `gl artifacts` store there), and `token_cmd` may need the user's keychain or home directory. A Keeper Commander `token_cmd` writes Commander's data directory (`~/.keeper`, else `~/.local/share/.keeper`) or keychain on every call, so when the user keeps the token in Keeper, request that write access before the first call: a blocked write lets that call succeed and breaks Commander's next sign-in. If a `gl` call fails with a connection error, a cache-directory error, or `token_cmd exited N`, request that access under the client's approval policy (or point `XDG_CACHE_HOME` at a writable directory) before reporting GitLab as unreachable or the secret store as broken.

`gl` is `<plugin-root>/scripts/gl`. Resolve it to an absolute path once per turn and use that. It needs only `python3` ≥ 3.9 — Amazon Linux 2023's default works.

## Discovery (once per turn, before anything else)

```bash
gl version
```

- **Exit 0** → JSON with `version`, `enterprise`, `url`. Proceed. A stderr warning about the version means the references may be slightly off — say so if something 404s.
- **Exit 2, "no GitLab URL configured" / "no token_cmd configured"** → the user hasn't run `gitlab-client-setup`. Say so and stop; don't ask for a token in chat.
- **Exit 2, "token_cmd exited N"** → their secret-store command failed; show the stderr `gl` printed (it never contains the token) and stop. In a sandboxed client, first retry once with the access described under Client compatibility.
- **Exit 1** → network/HTTP problem; the message names the host and the status. Suggest `gitlab-client-doctor`.

Never read `~/.claude/gitlab-client.local.md` or `.claude/gitlab-client.local.md` yourself and never run `token_cmd` — `gl` does both internally so the token never enters the transcript.

## Which project?

`gl` resolves the project in this order: `--project group/name` (or a pasted GitLab URL) → `project:` in `.claude/gitlab-client.local.md` → the `origin` remote of the current repo (`--remote NAME` for another remote). `gl project` shows the result. Without git (bare Amazon Linux 2023) the remote can't be read and there is no repo root to find: `gl` reads `.claude/gitlab-client.local.md` from the current directory, so run it from the project root or pass `--project`. In `gl api` paths write `:project` and it expands to the URL-encoded path:

```bash
gl api GET /projects/:project/merge_requests state=opened --fields iid,title,author.username,web_url
gl api GET /projects/:project/merge_requests --project other-group/other-repo --fields iid,title
```

A 404 on project resolution says *not found or not visible*; ask the user for the path rather than guessing.

## Command cheat-sheet

```bash
gl api GET|POST|PUT PATH [k=v | k:=json | k[]=v ...] [--all] [--max N] [--fields a,b.c] [--out FILE] [--json BODY]
gl project                      # {id, path_with_namespace, default_branch, web_url, resolved_from: flag|settings|remote:<name>}
gl log JOB_ID [--tail N|--head N|--grep RE [-C N]|--sections|--section NAME] [--raw] [--refresh]
gl diff MR_IID [--files] [--file PATH] · gl diff --commit SHA · gl diff --range A..B [--straight]
gl artifacts JOB_ID [--list|--extract DIR|--file PATH [--out FILE]] · gl artifacts --ref REF --job NAME …
```

Params: `key=value` is a string, `key:=<json>` is typed (`squash:=true`, `reviewer_ids:='[12]'`, `variables:='[{"key":"ENV","value":"staging"}]'`), `key[]=v` repeats. GET params become the query string; POST/PUT params become a JSON body. Exit codes: 0 ok · 1 HTTP/network · 2 config/usage · 3 refused by the write policy.

## Write policy (non-negotiable)

`gl` refuses everything not on its allow-list with exit 3; these rules sit on top of that.

| Class | What | Rule |
|---|---|---|
| **Refused** | merge, approve/unapprove, rebase (also as `/merge`, `/approve`, `/unapprove`, `/rebase` quick actions in a comment or description — `gl` exits 3), delete anything, erase job logs, repository writes (branches/tags/files — use git locally), project/group/member/variable/hook/protected-branch settings, users, tokens, `sudo` | Name the operation and hand it back to the user. Don't work around it with curl. |
| **Confirm first** | create MR, update MR (title/description/labels/reviewers/close/reopen), post a note, start a discussion, reply to a thread, resolve a thread | Show `project!iid`, the exact body/title text verbatim, and the command; send only after an explicit yes. One confirmation per distinct payload, even when the user said "just do it". |
| **Go and report** | retry/cancel a job or pipeline, play a manual job, trigger a pipeline (echo the ref + variables), keep artifacts | Run it, then report the new job/pipeline id, status, and `web_url`. |

Why the split: the confirm class is visible to colleagues and hard to unsay; the go class is cheap and reversible. Details and the error semantics (404 ambiguity, 403 role vs scope): [`references/safety-perimeter.md`](references/safety-perimeter.md).

## Context economy

- Lists: always `--fields`; a bare MR or pipeline object is ~2 KB. `--all` only with a filter (`state=opened`, `ref=`, `updated_after=`, `source_branch=`).
- Job logs: never `gl log … --tail 0` for a log you haven't sized; start with `--sections`, then `--section step_script --tail 80` or `--grep 'error|failed' -C 3`.
- Diffs: `gl diff N --files` first; then `--file PATH` for the files that matter.
- Raw files: unknown size → `gl api GET /projects/:project/repository/files/<path%2Fencoded> ref=<ref> --fields size,encoding` first; large → `…/raw ref=<ref> --out /tmp/x` and read it (see repo-browsing).

## Inline or dispatch?

- One lookup, one write, one file → inline with `gl`.
- "Why is CI red", failed-pipeline triage, anything that reads logs from more than one job or needs 5+ calls → dispatch the shared [`pipeline-debugging`](references/pipeline-debugging.md) procedure. In Claude Code it is the `pipeline-debugger` subagent; elsewhere, delegate it to an available subagent with the procedure's absolute path and the same inputs, or follow it inline. Give it the absolute `gl` path, the project path, the identifiers you have (pipeline id / job id / branch / MR iid), and the question. It returns a short report and never writes.

## References — load the one for the current step

- [`references/local-settings.md`](references/local-settings.md) — the two settings files, `token_cmd` examples, precedence, env overrides.
- [`references/repo-browsing.md`](references/repo-browsing.md) — tree (keyset), files (`ref` required), commits, compare, branches/tags, code search feature-detect.
- [`references/merge-requests.md`](references/merge-requests.md) — list/read, diffs, discussions vs notes, create (`Draft:` prefix), update (`add_labels`, `reviewer_ids`), "the MR for my branch".
- [`references/pipelines.md`](references/pipelines.md) — pipelines/jobs/bridges, logs, retry/cancel/play, trigger, test reports, artifacts.
- [`references/safety-perimeter.md`](references/safety-perimeter.md) — allow-list, confirmation format, error semantics.
- [`references/pipeline-debugging.md`](references/pipeline-debugging.md) — the read-only failed-pipeline triage procedure and its report format (delegated or inline).
- [`references/v15-compat.md`](references/v15-compat.md) — params/routes that don't exist on 15.x (or only from a later 15.x minor) and the deprecated-but-present fields to prefer.

## When to step out

- Issues, epics, wiki, snippets, releases — not covered; say so.
- Merging, approving, rebasing, deleting — refused; the user does it in the GitLab UI or with git.
- GitHub / Bitbucket / gitlab.com SaaS questions — wrong skill (the client would work against gitlab.com, but the references target self-managed 15.x).
- `python-gitlab` / `glab` / CI YAML authoring — not this skill's job.
