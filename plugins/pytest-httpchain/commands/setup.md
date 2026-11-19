---
description: Configure pytest-httpchain for this project — confirm the package is a dev dependency (guiding the install, never running it), optionally set the `httpchain_suffix` pytest ini option and scaffold an example scenario. Conservative — it writes only files you approve and never installs packages or makes HTTP calls.
allowed-tools:
  - Bash(test -f *)
  - Bash(test -d *)
  - Bash(test -x *)
  - Bash(poetry env info --executable)
  - Bash(poetry config virtualenvs.create)
  - Bash(poetry env info --no-ansi)
  - Bash(python* -B -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Bash(*/bin/python* -B -c "from importlib.metadata import version; print(version('pytest-httpchain'))")
  - Read
  - Glob
  - Write
  - Edit
  - AskUserQuestion
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/pytest-httpchain-setup/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.
