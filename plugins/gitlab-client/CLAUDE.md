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
- `scripts/gl` — bash launcher; runs the package from source with `python3`, works from any cwd.
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
- `scripts/gitlab-client/tests/` — `conftest.py` (`StubOpener` · `FakeResponse` · `client` fixture · `run_gl` helper); `test_version.py` · `test_settings.py` · `test_http.py` · `test_project.py` · `test_cli.py` · `test_log.py` · `test_diff.py` · `test_artifacts.py`; the trace and artifact-zip fixtures are built in-test (no `fixtures/` directory on disk).

## Commands

```bash
claude plugin validate .                              # marketplace
claude plugin validate plugins/gitlab-client          # this plugin
cd scripts/gitlab-client && uv sync --group dev && uv run pytest -q   # unit tests (stub transport, no GitLab)
GITLAB_CLIENT_LIVE=1 GITLAB_CLIENT_URL=… GITLAB_CLIENT_TOKEN=… uv run pytest -q tests/test_live.py   # optional live smoke (added in Task 14)
./scripts/gl version                                  # run the CLI without a venv
```

CI runs the validations and the pytest suite on push/PR via `.github/workflows/validate.yml` — it discovers `pyproject.toml` + `tests/` automatically.

## Gotchas

- **The token never touches a command line.** `settings.resolve_token` runs `token_cmd` in-process; error paths show stderr only. Don't add a `--token` flag.
- **The write allow-list lives in two places.** `http.WRITE_ALLOW` (enforced) and `references/safety-perimeter.md` (documented). Change both; `tests/test_http.py::ALLOWED/REFUSED` pins the code side.
- **`/repository/tree` is keyset-only** since 15.0 — `Client.paginate` switches automatically on that path; don't add `page=` there.
- **Cross-host redirects drop auth.** `AuthStrippingRedirectHandler` — artifact/trace downloads 302 to object storage. Keep `stream_to` downloads going through `Client.request`.
- **References target 15.11.** `v15-compat.md` is the list of things not to add; when the instance is upgraded, re-verify Appendix A of the spec before editing the references.
- **Subagent rules are duplicated, not linked.** `agents/pipeline-debugger.md` repeats the perimeter because subagents don't inherit skill context.
- **Version bumps touch four files:** `.claude-plugin/plugin.json`, the repo-root `marketplace.json` entry, `scripts/gitlab-client/pyproject.toml`, `uv.lock` — plus `src/gitlab_client/__init__.py::__version__` (pinned to pyproject by `tests/test_version.py`).
