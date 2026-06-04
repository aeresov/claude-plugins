# pytest-httpchain setup checklist

Shared by `/pytest-httpchain:setup` and `/pytest-httpchain:doctor` so the two can't drift. Each check has a probe and **remediation text to print verbatim** on failure. Run checks in order; a check that depends on an earlier one is **SKIP**ped when that earlier one fails.

All probes run against the **current project root**. The package must be available in the same environment `pytest` runs under — so prefer the project runner: `uv run …` when a `uv.lock` or `[tool.uv]` is present, otherwise the active interpreter.

---

## Check 1 — Python project + package manager

**Probe.** Look for a project file in the project root and identify the manager:
- `uv.lock` or `[tool.uv]` in `pyproject.toml` → **uv** (runner: `uv run`)
- `[tool.poetry]` in `pyproject.toml` → **poetry** (runner: `poetry run`)
- `[tool.pdm]` in `pyproject.toml` → **pdm** (runner: `pdm run`)
- `pyproject.toml`/`setup.cfg`/`requirements*.txt` only, or an active venv → **pip** (runner: the active `python`/`pytest`)

**Remediation (no Python project found):**
> No Python project detected here. pytest-httpchain is a pytest plugin, so it needs a Python project to live in. Create one (`uv init`, `poetry init`, or a venv + `pyproject.toml`) or `cd` into the project that owns the tests, then re-run.

## Check 2 — pytest-httpchain installed in the test env

**Probe** (substitute the Check 1 runner; example shows uv):
```bash
uv run python -c "from importlib.metadata import version; print(version('pytest-httpchain'))"
```
Pass = it prints a version. This interpreter must be the one `pytest` uses — that is what confirms the `pytest11` plugin is actually registered for collection.

**Remediation (not installed), printed for the detected manager:**
> pytest-httpchain isn't installed in this project's test environment. Add it as a dev dependency, then re-run:
> - uv:     `uv add --group dev pytest-httpchain`
> - poetry: `poetry add --group dev pytest-httpchain`
> - pdm:    `pdm add -dG dev pytest-httpchain`
> - pip:    `pip install pytest-httpchain` (and record it in your dev requirements)

## Check 3 — scenarios discoverable

**Probe.** Read the `suffix` ini option (default `http`) from `[tool.pytest.ini_options]` in `pyproject.toml`, or `pytest.ini`/`tox.ini`/`setup.cfg`. Then find files matching `test_*.<suffix>.json` under the project (typically `tests/`). Report the count. **Zero is not a failure** — it's an empty suite; say so.

**Remediation (informational, when zero found):**
> No `test_*.<suffix>.json` scenarios found (suffix = `<suffix>`). That's fine for a fresh setup — write one (the `pytest-httpchain` skill covers the format) or run `/pytest-httpchain:setup` to scaffold an example.

## Check 4 — scenarios validate clean

**Probe** (SKIP if Check 2 failed or Check 3 found zero). Shallow validation, no network:
```bash
uv run pytest-httpchain validate --format json <scenario> [<scenario> ...]
```
Pass = exit 0 and every file `valid`. Parse the JSON; surface any `HTTPCHAINxxx` diagnostics (code + severity + message) per file.

**Remediation (one or more invalid):**
> `<N>` scenario(s) failed validation — see the `HTTPCHAINxxx` diagnostics above. Fix each error and re-run, or hand the scenario to the `httpchain-author` subagent to repair. Run `--deep --syspath <dir>` to additionally check that `module:func` references resolve.
