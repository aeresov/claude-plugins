---
description: Diagnose the pytest-httpchain setup — Python project + package manager, package installed in the test env (+ version), scenarios discoverable, and every scenario passes `validate` — and report what's missing. Read-only — it runs `pytest-httpchain validate` (static, no network) and version probes, writes nothing, and dispatches no agent.
allowed-tools:
  - Bash(test -f *)
  - Bash(test -d *)
  - Bash(test -x *)
  - Bash(poetry env info --executable)
  - Bash(poetry config virtualenvs.create)
  - Bash(poetry env info --no-ansi)
  - Bash(python* -B -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(*/bin/python* -B -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(python* -B -m pytest_httpchain.cli validate *)
  - Bash(*/bin/python* -B -m pytest_httpchain.cli validate *)
  - Read
  - Glob
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/pytest-httpchain-doctor/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.
