---
description: Diagnose the pytest-httpchain setup — Python project + package manager, package installed in the test env (+ version), scenarios discoverable, and every scenario passes `validate` — and report what's missing. Read-only — it runs `pytest-httpchain validate` (static, no network) and version probes, writes nothing, and dispatches no agent.
allowed-tools:
  - Bash(test -f *)
  - Bash(test -d *)
  - Bash(python -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(uv run python -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(poetry run python -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(pdm run python -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(pytest-httpchain validate *)
  - Bash(uv run pytest-httpchain validate *)
  - Bash(poetry run pytest-httpchain validate *)
  - Bash(pdm run pytest-httpchain validate *)
  - Read
  - Glob
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/pytest-httpchain-doctor/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.
