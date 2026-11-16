# CLAUDE.md

Developer notes for working inside `gitlab-client`. User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## Shared client support

This plugin supports Claude Code and Codex. `plugin.json` is the portable Codex manifest; `.claude-plugin/plugin.json` is the Claude manifest. Both catalogs and both manifests must agree on name, description and version. Setup/doctor procedures live in the named skills; `commands/` contains Claude wrappers. Keep changes to the shared procedures in `skills/`, not in those wrappers.

## What this plugin is

A bundle that makes the assistant an effective, *safe* user of a self-managed GitLab 15.x — repos, merge requests, CI — through `gl`, a small bundled REST v4 client (pure stdlib, `python3` ≥ 3.9 — bare Amazon Linux 2023's default — no venv at runtime). Reads are free; POST/PUT is restricted to an allow-list (create/update MRs, comments, retry/cancel/play/trigger); merge, approve, delete, and settings changes are refused at the code level (exit 3). The plugin contributes the skill plus seven references (one is the shared triage procedure), the `gitlab-client-setup` and `gitlab-client-doctor` skills (wrapped by the Claude commands `/gitlab-client:setup` and `/gitlab-client:doctor`), a `pipeline-debugger` subagent wrapper, and the `scripts/gitlab-client/` uv project that implements `gl`.

## Layout

- `.claude-plugin/plugin.json` · `plugin.json` — Claude and portable (Codex) manifests. Name/description/version mirror both repo-root catalog entries; the portable one also carries the license and `onboardingSkill` (`gitlab-client-setup`).
- `CLAUDE.md` · `README.md` · `setup-checklist.md` — this file; user docs; the shared checklist (8 checks + remediation text) read by both setup/doctor skills so they can't drift. It lives at the plugin root so it isn't itself a slash command.
- `skills/gitlab-client-setup/SKILL.md` · `skills/gitlab-client-doctor/SKILL.md` — the shared setup (interactive configurator; runs the static checks it owns: 1, 2, 3, 8) and doctor (health check; reports all 8) procedures. `commands/setup.md` · `commands/doctor.md` are their Claude wrappers (`/gitlab-client:setup`, `/gitlab-client:doctor`) and keep the `allowed-tools`.
- `agents/pipeline-debugger.md` — Claude wrapper around `skills/gitlab-client/references/pipeline-debugging.md` (failed-pipeline triage; read-only, returns a short report). Codex delegates that procedure through its available subagents or runs it inline.
- `skills/gitlab-client/SKILL.md` — entry point. Owns the write-policy classes (refused / confirm / go-and-report), discovery (`gl version` once per turn), project resolution, context economy, and the inline-vs-dispatch rule.
- `skills/gitlab-client/references/{local-settings,repo-browsing,merge-requests,pipelines,safety-perimeter,v15-compat}.md` — reference-style docs (purpose → command → gotcha), loaded on demand, each self-sufficient; `pipeline-debugging.md` — the shared triage procedure.
- `scripts/gl` — bash launcher; runs the package from source with `python3`, works from any cwd. Deliberately not `python3 -m`: it replaces `sys.path[0]` (which `-m` would set to the *current directory*) with the plugin's `src/`, so a checkout containing a decoy `gitlab_client/` can't hijack the CLI (`tests/test_launcher.py`).
- `scripts/gitlab-client/pyproject.toml` · `uv.lock` · `.python-version` · `Makefile` — uv project for `gl` (dev deps `pytest`, `ruff`, `ty`; zero runtime deps; the dev venv is pinned to 3.9). The `Makefile` is identical across all three in-tree projects.
- `scripts/gitlab-client/src/gitlab_client/`:
  - `__init__.py` — `__version__`
  - `__main__.py` — `sys.exit(main())`
  - `errors.py` — `GlError`(1) / `ConfigError`(2) / `PolicyError`(3) / `HttpError`(1) — the number is the exit code (0 ok · 1 HTTP/network · 2 config/usage · 3 write policy)
  - `settings.py` — `parse_frontmatter` · `load_settings` · `resolve_token`
  - `http.py` — `Client` · `Response` · `AuthStrippingRedirectHandler` · `parse_params` · `build_query` · `check_write_policy` · `substitute_project` · `project_fields` · `describe_error`
  - `project.py` — `parse_remote_url` · `parse_project_ref` · `git_output`/`git_toplevel`/`git_remote_url` · `resolve_project` · `Project`
  - `log.py` — `clean_line` · `parse_trace` · `Section` · `tail`/`head`/`grep` · `sections_summary` · `section_body` · `fetch_trace` · `header_line`
  - `diff.py` — `file_status` · `render_file` · `render_diffs` · `render_files` · `render_compare` · `mr_diffs` · `commit_diff` · `compare`
  - `artifacts.py` — `encode_artifact_path` · `download_archive` · `fetch_file` · `list_archive` · `extract_archive` · cache paths
  - `cli.py` — `Context` · `build_parser` · `cmd_api`/`cmd_project`/`cmd_version`/`cmd_log`/`cmd_diff`/`cmd_artifacts` · `main`
- `scripts/gitlab-client/tests/` — `conftest.py` (`StubOpener` · `FakeResponse` · `client` fixture · `run_gl` helper); `test_version.py` · `test_settings.py` · `test_http.py` · `test_project.py` · `test_cli.py` · `test_log.py` · `test_diff.py` · `test_artifacts.py` · `test_launcher.py` (runs `scripts/gl` in a subprocess) · `test_live.py` (opt-in, needs `GITLAB_CLIENT_LIVE=1`); the trace and artifact-zip fixtures are built in-test (no `fixtures/` directory on disk).

## Commands

```bash
claude plugin validate .                              # marketplace
claude plugin validate plugins/gitlab-client          # this plugin
cd scripts/gitlab-client && make check                # ruff + ty + pytest (stub transport, no GitLab)
cd scripts/gitlab-client && make tidyup               # ruff autofix + format
GITLAB_CLIENT_LIVE=1 GITLAB_CLIENT_URL=… GITLAB_CLIENT_TOKEN=… uv run pytest -q tests/test_live.py   # opt-in live smoke: GET /metadata + /user
./scripts/gl version                                  # run the CLI without a venv
```

CI runs the validations and then this project's own `make check` (ruff, format, ty, pytest) via `.github/workflows/validate.yml` — it discovers `pyproject.toml` + `tests/` automatically.

## Gotchas

- **The token never touches a command line.** `settings.resolve_token` runs `token_cmd` in-process; error paths show stderr only. Don't add a `--token` flag.
- **The write allow-list lives in two places.** `http.WRITE_ALLOW` (enforced) and `references/safety-perimeter.md` (documented). Change both; `tests/test_http.py::ALLOWED/REFUSED` pins the code side. `check_write_policy` also refuses paths that wouldn't round-trip (`#`, whitespace, non-ASCII, dot segments) on *every* verb — urllib truncates at `#`, so `PUT /projects/1#/merge_requests/5` would otherwise match the allow-list and hit `PUT /projects/1`. The `sudo` parameter is refused in `cli.cmd_api` for params, a query string inside PATH (split on `;` too — GitLab 15.x runs Rack 2, which does), and `--json` bodies. `http.check_body_policy` refuses the merge/approve/unapprove/rebase quick actions and `merge_request_diff_head_sha` in any POST/PUT parameter: GitLab runs quick actions in MR descriptions and notes, all allow-listed, so without it the path allow-list alone wouldn't stop a merge.
- **`/repository/tree` is keyset-only** since 15.0 — `Client.paginate` switches automatically on that path; don't add `page=` there.
- **Redirects and Link URLs never carry the token off-origin.** `AuthStrippingRedirectHandler` drops auth unless the target has the API's scheme *and* host (object-storage 302s, and an https→http downgrade behind a misconfigured proxy); `Client.same_origin` re-anchors every `Link: rel="next"` URL on our scheme + host before `paginate` follows it. Keep `stream_to` downloads going through `Client.request`.
- **References target the 15.x series, verified on 15.2.5 CE.** The spec was researched against the archived 15.11 docs; `v15-compat.md` carries both the post-15.11 things not to add and the intra-15.x minimums (`/diffs` 15.7, `detailed_merge_status` 15.6, `/personal_access_tokens/self` 15.5 …). `diff.mr_diffs` falls back to `/changes` on a 404. When the instance is upgraded, re-verify Appendix A of the spec before editing the references.
- **Shared triage procedure.** Put changes in `skills/gitlab-client/references/pipeline-debugging.md`; the Claude wrapper and any Codex delegate must read it in their own context, because subagents don't inherit skill context.
- **The Python 3.9 floor is load-bearing.** Bare Amazon Linux 2023's `python3` is 3.9 and `gl` runs on it without a venv. `.python-version` pins the dev venv (and CI's `uv sync`) to 3.9 so `make check` tests the floor; don't raise it, and don't use 3.10+ syntax or stdlib. pytest stays on 8.x until the floor moves (the lock forks: 8.4 on 3.9, 9.x above), and ty misreads pytest 8's decorated `skip()`/`fail()`, so such a call carries a targeted `# ty: ignore[...]`. Without git, `gl` can't find the repo root and reads the project file from the current directory; `resolve_project` says when git is missing.
- **A project-level `.claude/gitlab-client.local.md` may only set `project`.** `settings.load_settings` drops `url` and `token_cmd` from it with a warning (`PROJECT_FILE_IGNORED`). A project `url` alone would send the user-level token to that host, and a project `token_cmd` would run inside `gl`, where Claude Code's per-command permission prompt can't see it. A repo on a second instance uses `GITLAB_CLIENT_URL` + `GITLAB_CLIENT_TOKEN` instead (`tests/test_settings.py::test_project_file_cannot_set_url_or_token_cmd`).
- **Keeper means Keeper Commander (`keeper`), never Keeper Secrets Manager (`ksm`).** KSM needs a Secrets Manager application, an enterprise feature the owner doesn't have. The Commander `token_cmd` in `references/local-settings.md` is load-bearing word for word (`cd /`, because Commander runs a cwd file named like the command and reads a cwd `config.json`; UID not title; `</dev/null` + `setsid -w` + `--batch-mode`, so no prompt can wait on stdin or the terminal; `--fail-on-throttle`; `flock -w 50 -E 75`, so parallel sign-ins take turns and a waiter gives up inside `gl`'s 60 s); the setup skill and the README quote it — change all three together. The sign-in prerequisites are load-bearing too: persistent login *and* a logout timeout (`keeper this-device timeout 30d`), and in a sandbox, write access to Commander's data directory before the first call (a blocked write succeeds once, then breaks the next sign-in). It was checked against Commander 18.1.10's source and binary (never against a signed-in vault), and it starts Commander the way sso-auto-login's `keeper.py` does: stdin `/dev/null`, a session of its own, a cwd with nothing in it, the same global flags.
- **Version bumps:** both manifests (`.claude-plugin/plugin.json`, `plugin.json`), both catalog entries (`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`), `scripts/gitlab-client/pyproject.toml`, `uv.lock` — plus `src/gitlab_client/__init__.py::__version__` (pinned to pyproject by `tests/test_version.py`). `scripts/check-version-sync.sh` checks the metadata; `uv sync --locked` checks the lock.

## Verified against a live instance (2026-08-27, GitLab 15.2.5 CE)

Spec Appendix B probes, run with `gl` against a read-only project and a throwaway project (writes). The instance turned out to be **15.2.5 CE**, not the 15.11 EE the spec assumed — hence the intra-15.x table in `v15-compat.md`.

1. **Scopes of `/metadata` and `/personal_access_tokens/self`:** `/metadata` and `/user` work with both `api` and `read_api`. `/personal_access_tokens/self` returns **401** for both — it doesn't exist before 15.5; doctor check 6 WARNs on 401/403/404.
2. **`Range` on `GET /jobs/:id/trace`:** ignored — `Range: bytes=-1024` returns 200 with the full body; `Content-Type: text/plain`. `gl log` keeps downloading whole traces.
3. **Project code search `scope=blobs`:** works on CE without Elasticsearch (basic search) — results carry `path` and `startline`.
4. **Retry / play:** `POST …/jobs/<id>/play` on a *finished* `when: manual` job creates and runs a new job (cancelled straight after). `POST …/jobs/<id>/retry` on a never-run manual job and on a job that was already re-played → **403 `Job is not retryable`**. `cancel` on a pending job → 200, `status: canceled`.
5. **Multiple `reviewer_ids`:** not run — no throwaway MR existed in a project the user owns.
6. **Unknown label via `add_labels`:** not run — same reason.
7. **Artifacts:** `gl artifacts <job> --list` and `--file` work. Downloads are **proxied** here (200, no `Location`), so the cross-host redirect stripping in `AuthStrippingRedirectHandler` is covered by unit tests only.

Also observed: `GET …/merge_requests/<iid>/diffs` → 404 (15.7+); `/changes` works and reports `overflow`. `detailed_merge_status` is `null`; `merge_status` is populated. `order_by=version` on tags → 400. Keyset tree paging, `/bridges`, `test_report_summary`, `/members/all`, `/labels` all fine. The api token's user is Developer (30) on the read-only project.
