---
name: pytest-httpchain-doctor
description: "Diagnose the pytest-httpchain setup — Python project + package manager, package installed in the test env (+ version), scenarios discoverable, and every scenario passes `validate` — and report what's missing. Read-only — it runs `pytest-httpchain validate` (static, no network) and version probes, writes nothing, and dispatches no agent."
---

Resolve `../..` from this SKILL.md's directory to the absolute plugin root. Substitute that path for `<plugin-root>` in the instructions and commands below; do not assume a plugin-root shell variable is set. Keep the working directory at the consuming project root.

In Codex, select `$pytest-httpchain:pytest-httpchain-setup` or `$pytest-httpchain:pytest-httpchain-doctor`. In Claude Code, the equivalent commands are `/pytest-httpchain:setup` and `/pytest-httpchain:doctor`.

You are running `pytest-httpchain-doctor`: a health check. Write no files, dispatch no agent, make no HTTP calls. Follow the host client's approval policy. `validate` is static and touches no network.

## Steps

1. Read the shared checklist at `<plugin-root>/setup-checklist.md` — it defines checks 1–4 with probes and remediation text.

2. Run checks **1–4 in order** against the project root, using the runner detected in check 1:
   - 1 Python project + package manager
   - 2 pytest-httpchain installed in the test env (+ version) — SKIP 3–4 if it fails
   - 3 scenarios discoverable (`test_*.<suffix>.json`, suffix from `httpchain_suffix` in the pytest config table pytest reads; report the suffix and count) — zero is **n/a**, not a failure; a leftover bare `suffix` key is a **FAIL**
   - 4 scenarios validate clean (`validate --format json`) — SKIP if 2 failed or 3 found zero. Only error-severity diagnostics make a file invalid; list warnings as `warning`, never as errors

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
   PASS  2. pytest-httpchain installed — 0.15.0
   PASS  3. scenarios discoverable — suffix 'http' (default), 4 found
   FAIL  4. scenarios validate — 1 of 4 invalid
         → tests/test_login.http.json: error [HTTPCHAIN001]: Duplicate stage names found: ['login']
         → tests/test_orders.http.json: warning [HTTPCHAIN005]: Stage 'cleanup' has no response validation (no verify step)
         → <check 4 remediation, verbatim>
   ```

4. End with a one-line summary:
   - All green → `OK — pytest-httpchain ready · <manager> · <N> scenario(s) valid`.
   - Package missing → `Not set up — add pytest-httpchain as a dev dependency (see check 2), then re-run, or pytest-httpchain-setup`.
   - Otherwise → `<n> issue(s) — see the FAIL lines above`.

Keep it terse. Don't fix anything here — point at `pytest-httpchain-setup` or the shared authoring procedure only where the checklist's remediation already does.
