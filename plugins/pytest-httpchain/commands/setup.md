---
description: Configure pytest-httpchain for this project — confirm the package is a dev dependency (guiding the install, never running it), optionally set the `httpchain_suffix` pytest ini option and scaffold an example scenario. Conservative — it writes only files you approve and never installs packages or makes HTTP calls.
allowed-tools: Bash(test -f *), Bash(test -d *), Bash(uv run python -c *), Bash(python -c *), Bash(poetry run python -c *), Bash(pdm run python -c *), Read, Glob, Write, Edit, AskUserQuestion
---

You are running `/pytest-httpchain:setup`: an interactive configurator. You may write **only** files the user approves — an `httpchain_suffix` line in the project's existing pytest config and/or a scaffolded example scenario. You will **not** install or modify dependencies (you guide the user to run the install), **not** run `validate` or `pytest`, and **not** dispatch the subagent.

First read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` — it defines the checks and the remediation text. Use that text verbatim on failures.

## Flow

### 1. Project + package manager
Run check 1. If no Python project is found, print its remediation text and **stop**. Otherwise note the detected manager and its runner (`uv run` / `poetry run` / `pdm run` / active `python`) for the steps below.

### 2. Is the package installed?
Run check 2's probe with the detected runner. If it prints a version, say so and go to step 3. If not, print check 2's remediation for the detected manager and tell the user to run the add command themselves — suggest typing `! <command>` so its output lands in this session — then **stop** and ask them to re-run `/pytest-httpchain:setup` once it's added. Do **not** add the dependency yourself.

### 3. Test file suffix (optional)
Scenarios are discovered as `test_<name>.<suffix>.json`; the `httpchain_suffix` ini option defaults to `http`. That name needs pytest-httpchain ≥0.10 — if step 2 found an older version, suggest upgrading and skip this step. Locate the pytest config table pytest actually reads and the current value exactly as check 3 does. If that table still has a bare `suffix` key (ignored since 0.11), say so and offer to rename it to `httpchain_suffix` as part of this step. Ask via **AskUserQuestion** — "Keep the default test suffix `http`, or set a custom one?":
- **Keep `http`** — most projects; change nothing.
- **Set custom** — ask for the value (letters, digits, `_`, `-`; at most 32 characters — anything else aborts pytest), then add/replace `httpchain_suffix` **in the table check 3 found**, replacing any bare `suffix` key there: `httpchain_suffix = "<value>"` in a TOML table (`[tool.pytest]`, `[tool.pytest.ini_options]`, `pytest.toml`'s `[pytest]`), unquoted `httpchain_suffix = <value>` in an INI file (`pytest.ini`, `tox.ini`, `setup.cfg`). **Never add a second pytest table** — pytest 9 refuses to start when `pyproject.toml` has both `[tool.pytest]` and `[tool.pytest.ini_options]`, and a table in a lower-precedence file is silently ignored. Only when no pytest config exists at all, create `[tool.pytest.ini_options]` in `pyproject.toml`. Restate the file, table, and resulting discovery pattern (`test_<name>.<value>.json`) and confirm before writing.

### 4. Scaffold an example (optional)
Ask via **AskUserQuestion** — "Scaffold a minimal example scenario + conftest?":
- **Yes** — write into `tests/` if it exists, else ask where. Create a `test_example.<suffix>.json` and, if no `conftest.py` exists there, a tiny one. Use a self-contained scenario that validates without a server:
  ```json
  {
    "description": "Example pytest-httpchain scenario — replace with your API.",
    "substitutions": [
      { "vars": { "base": "{{ env('API_URL', 'https://httpbin.org') }}" } }
    ],
    "stages": [
      {
        "name": "get",
        "request": { "url": "{{ base }}/get", "params": { "q": "hello" } },
        "response": [
          { "verify": { "status": 200 } },
          { "save": { "jmespath": { "echoed": "args.q" } } },
          { "verify": { "expressions": ["{{ echoed == 'hello' }}"] } }
        ]
      }
    ]
  }
  ```
  ```python
  # conftest.py — fixtures available to scenarios go here.
  ```
- **No** — change nothing.

### 5. Summary
Print what you wrote (paths), the detected manager/runner, and the active `httpchain_suffix` (and which config table it's in). End with: "Done. Run `/pytest-httpchain:doctor` to confirm the install, discovery, and that every scenario validates. Ask me to write a scenario any time — I'll validate it and use the `httpchain-author` subagent for multi-stage ones."
