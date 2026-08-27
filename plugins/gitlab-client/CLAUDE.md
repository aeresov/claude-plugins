# CLAUDE.md

Developer notes for working inside `gitlab-client`. User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## What this plugin is

A bundle that makes Claude an effective, *safe* user of a self-managed GitLab 15.x — repos, merge requests, CI — through `gl`, a small bundled REST v4 client (pure stdlib, `python3` ≥ 3.10, no venv at runtime). Reads are free; POST/PUT is restricted to an allow-list (create/update MRs, comments, retry/cancel/play/trigger); merge, approve, delete, and settings changes are refused at the code level (exit 3). The plugin contributes the skill plus six references, a `pipeline-debugger` subagent, the `/gitlab-client:setup` and `/gitlab-client:doctor` commands, and the `scripts/gitlab-client/` uv project that implements `gl`.

## Layout

- `.claude-plugin/plugin.json` — manifest (name/description/version/license mirror the repo-root `marketplace.json` entry).
- `CLAUDE.md` · `README.md` · `setup-checklist.md` — this file; user docs; the shared checklist (8 checks + remediation text) read by both commands so they can't drift. It lives at the plugin root so it isn't itself a slash command.
- `commands/setup.md` · `commands/doctor.md` — `/gitlab-client:setup` (interactive configurator; runs the static checks it owns: 1, 2, 3, 8) and `/gitlab-client:doctor` (health check; reports all 8).
- `agents/pipeline-debugger.md` — context-isolated failed-pipeline triage subagent; read-only, returns a short report.
- `skills/gitlab-client/SKILL.md` — entry point. Owns the write-policy classes (refused / confirm / go-and-report), discovery (`gl version` once per turn), project resolution, context economy, and the inline-vs-dispatch rule.
- `skills/gitlab-client/references/{local-settings,repo-browsing,merge-requests,pipelines,safety-perimeter,v15-compat}.md` — reference-style docs (purpose → command → gotcha), loaded on demand, each self-sufficient.
- `scripts/gl` — bash launcher; runs the package from source with `python3`, works from any cwd. Deliberately not `python3 -m`: it replaces `sys.path[0]` (which `-m` would set to the *current directory*) with the plugin's `src/`, so a checkout containing a decoy `gitlab_client/` can't hijack the CLI (`tests/test_launcher.py`).
- `scripts/gitlab-client/pyproject.toml` · `uv.lock` — uv project for `gl` (dev dep `pytest`; zero runtime deps).
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
cd scripts/gitlab-client && uv sync --group dev && uv run pytest -q   # unit tests (stub transport, no GitLab)
GITLAB_CLIENT_LIVE=1 GITLAB_CLIENT_URL=… GITLAB_CLIENT_TOKEN=… uv run pytest -q tests/test_live.py   # opt-in live smoke: GET /metadata + /user
./scripts/gl version                                  # run the CLI without a venv
```

CI runs the validations and the pytest suite on push/PR via `.github/workflows/validate.yml` — it discovers `pyproject.toml` + `tests/` automatically.

## Gotchas

- **The token never touches a command line.** `settings.resolve_token` runs `token_cmd` in-process; error paths show stderr only. Don't add a `--token` flag.
- **The write allow-list lives in two places.** `http.WRITE_ALLOW` (enforced) and `references/safety-perimeter.md` (documented). Change both; `tests/test_http.py::ALLOWED/REFUSED` pins the code side. `check_write_policy` also refuses paths that wouldn't round-trip (`#`, whitespace, non-ASCII, dot segments) on *every* verb — urllib truncates at `#`, so `PUT /projects/1#/merge_requests/5` would otherwise match the allow-list and hit `PUT /projects/1`. The `sudo` parameter is refused in `cli.cmd_api` for params, a query string inside PATH, and `--json` bodies.
- **`/repository/tree` is keyset-only** since 15.0 — `Client.paginate` switches automatically on that path; don't add `page=` there.
- **Redirects and Link URLs never carry the token off-origin.** `AuthStrippingRedirectHandler` drops auth unless the target has the API's scheme *and* host (object-storage 302s, and an https→http downgrade behind a misconfigured proxy); `Client.same_origin` re-anchors every `Link: rel="next"` URL on our scheme + host before `paginate` follows it. Keep `stream_to` downloads going through `Client.request`.
- **References target the 15.x series, verified on 15.2.5 CE.** The spec was researched against the archived 15.11 docs; `v15-compat.md` carries both the post-15.11 things not to add and the intra-15.x minimums (`/diffs` 15.7, `detailed_merge_status` 15.6, `/personal_access_tokens/self` 15.5 …). `diff.mr_diffs` falls back to `/changes` on a 404. When the instance is upgraded, re-verify Appendix A of the spec before editing the references.
- **Subagent rules are duplicated, not linked.** `agents/pipeline-debugger.md` repeats the perimeter because subagents don't inherit skill context.
- **A project-level `.claude/gitlab-client.local.md` can override `url` and `token_cmd`** (spec §3). That's convenient for a repo on a second instance, but it also means a hostile checkout could point `gl` at another host or run its own `token_cmd` — the same trust model as `mysql-client`'s `connection_cmd`. If that ever matters, restrict the project file to `project:` in `settings.load_settings`.
- **Version bumps touch four files:** `.claude-plugin/plugin.json`, the repo-root `marketplace.json` entry, `scripts/gitlab-client/pyproject.toml`, `uv.lock` — plus `src/gitlab_client/__init__.py::__version__` (pinned to pyproject by `tests/test_version.py`).

## Verified on the instance (2026-08-27, GitLab 15.2.5 CE at gitlabs.soundunited.com)

Spec Appendix B probes, run with `gl` against `heos/heos` (read-only) and `alexander.eresov/workarounds` (writes). The instance turned out to be **15.2.5 CE**, not the 15.11 EE the spec assumed — hence the intra-15.x table in `v15-compat.md`.

1. **Scopes of `/metadata` and `/personal_access_tokens/self`:** `/metadata` and `/user` work with both `api` and `read_api`. `/personal_access_tokens/self` returns **401** for both — it doesn't exist before 15.5; doctor check 6 WARNs on 401/403/404.
2. **`Range` on `GET /jobs/:id/trace`:** ignored — `Range: bytes=-1024` returns 200 with the full body; `Content-Type: text/plain`. `gl log` keeps downloading whole traces.
3. **Project code search `scope=blobs`:** works on CE without Elasticsearch (basic search) — results carry `path` and `startline`.
4. **Retry / play:** `POST …/jobs/<id>/play` on a *finished* `when: manual` job creates and runs a new job (id 881619 here — cancelled straight after). `POST …/jobs/<id>/retry` on a never-run manual job and on a job that was already re-played → **403 `Job is not retryable`**. `cancel` on a pending job → 200, `status: canceled`.
5. **Multiple `reviewer_ids`:** not run — no throwaway MR existed in a project the user owns.
6. **Unknown label via `add_labels`:** not run — same reason.
7. **Artifacts:** `gl artifacts <job> --list` and `--file` work. Downloads are **proxied** here (200, no `Location`), so the cross-host redirect stripping in `AuthStrippingRedirectHandler` is covered by unit tests only.

Also observed: `GET …/merge_requests/<iid>/diffs` → 404 (15.7+); `/changes` works and reports `overflow`. `detailed_merge_status` is `null`; `merge_status` is populated. `order_by=version` on tags → 400. Keyset tree paging, `/bridges`, `test_report_summary`, `/members/all`, `/labels` all fine. The api token's user is Developer (30) on `heos/heos`.
