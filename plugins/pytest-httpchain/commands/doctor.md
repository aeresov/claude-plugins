---
description: Diagnose the pytest-httpchain setup — Python project + package manager, package installed in the test env (+ version), scenarios discoverable, and every scenario passes `validate` — and report what's missing. Read-only — it runs `pytest-httpchain validate` (static, no network) and version probes, writes nothing, and dispatches no agent.
allowed-tools: Bash(test -f *), Bash(test -d *), Bash(uv run *), Bash(python -c *), Bash(poetry run *), Bash(pdm run *), Bash(pytest-httpchain *), Read, Glob
---

You are running `/pytest-httpchain:doctor`: a health check. Write no files, dispatch no agent, make no HTTP calls. The probes run the project's own runner (`uv run` etc.) and `pytest-httpchain validate` — arbitrary project commands, so they aren't pre-approved and may prompt for permission once each; that is expected. `validate` is static and touches no network.

## Steps

1. Read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` — it defines checks 1–4 with probes and remediation text.

2. Run checks **1–4 in order** against the project root, using the runner detected in check 1:
   - 1 Python project + package manager
   - 2 pytest-httpchain installed in the test env (+ version) — SKIP 3–4 if it fails
   - 3 scenarios discoverable (`test_*.<suffix>.json`; report the suffix and count) — zero is **n/a**, not a failure
   - 4 scenarios validate clean (`validate --format json`) — SKIP if 2 failed or 3 found zero

3. Print one line per check. Example with a missing package:
   ```
   PASS  1. python project — uv (runner: uv run)
   FAIL  2. pytest-httpchain installed
         → <check 2 remediation, verbatim, for uv>
   SKIP  3. scenarios discoverable — package not installed
   SKIP  4. scenarios validate — package not installed
   ```
   Example, all configured:
   ```
   PASS  1. python project — uv (runner: uv run)
   PASS  2. pytest-httpchain installed — 0.5.0
   PASS  3. scenarios discoverable — suffix 'http', 4 found
   FAIL  4. scenarios validate — 1 of 4 invalid
         → tests/test_login.http.json: error [HTTPCHAIN001]: Duplicate stage names found: ['login']
         → <check 4 remediation, verbatim>
   ```

4. End with a one-line summary:
   - All green → `OK — pytest-httpchain ready · <manager> · <N> scenario(s) valid`.
   - Package missing → `Not set up — add pytest-httpchain as a dev dependency (see check 2), then re-run, or /pytest-httpchain:setup`.
   - Otherwise → `<n> issue(s) — see the FAIL lines above`.

Keep it terse. Don't fix anything here — point at `/pytest-httpchain:setup` or the `httpchain-author` subagent only where the checklist's remediation already does.
