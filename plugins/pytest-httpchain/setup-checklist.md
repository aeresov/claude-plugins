# pytest-httpchain setup checklist

Shared by `pytest-httpchain-setup` and `pytest-httpchain-doctor` so the two can't drift. Each check has a probe and **remediation text to print verbatim** on failure. Run checks in order; a check that depends on an earlier one is **SKIP**ped when that earlier one fails.

All probes run against the **current project root**, using the **existing test interpreter** selected below. Never bootstrap or synchronize an environment during setup/doctor probes: even `uv run --no-sync` creates a missing `.venv`, and bare `uv run` can install packages and update the lockfile. Retain the selected interpreter's literal absolute path as `<test-python>`; do not resolve a venv's interpreter symlink to the system Python. Quote that path in shell commands and pass it to delegated work.

---

## Check 1 — Python project + package manager

**Probe.** Look for a project file in the project root, identify the manager, and locate its already-created test interpreter without running an environment-creating command:
- `uv.lock` or `[tool.uv]` in `pyproject.toml` → **uv**. Use `<project-root>/.venv/bin/python`, or the existing environment named by `UV_PROJECT_ENVIRONMENT` (resolve a relative value against the project root).
- `[tool.poetry]` in `pyproject.toml` → **poetry**. First try `poetry env info --executable`, which reports an existing venv's interpreter without creating it. A nonzero exit or empty output can also mean the project intentionally uses a non-venv environment: read `poetry config virtualenvs.create` **without a value**. Only if it reports `false`, run `poetry env info --no-ansi` and use the concrete `Executable` path in its **Base** section when its **Virtualenv** path/executable are `NA`. This is Poetry's selected existing interpreter; do not substitute whichever global `python` happens to be on PATH. If venv creation is enabled, or the report has no usable interpreter path, report a missing test environment. These queries never run a package-manager launcher or write configuration.
- `[tool.pdm]` in `pyproject.toml` → **pdm**. Read the existing interpreter path from `.pdm-python`, or use the project's existing `.venv/bin/python` or activated test venv. Preserve the project's existing environment settings; for a PEP 582 project, the local-package import path must already be configured. If the test environment cannot be identified, report it rather than falling back to a global installation.
- `pyproject.toml`/`setup.cfg`/`requirements*.txt` only, or an active venv → **pip**. Use the existing active test interpreter.

Use the platform's interpreter layout (for example `Scripts/python.exe` on Windows). A missing environment is a Check 2 failure, not permission to create one. Do not run `uv run`, `poetry run`, `pdm run`, `uvx`, or an install/sync command for these checks. Every CLI invocation below is `"<test-python>" -B -m pytest_httpchain.cli`; `-B` avoids creating bytecode caches.

**Remediation (no Python project found):**
> No Python project detected here. pytest-httpchain is a pytest plugin, so it needs a Python project to live in. Create one (`uv init`, `poetry init`, or a venv + `pyproject.toml`) or `cd` into the project that owns the tests, then re-run.

## Check 2 — pytest-httpchain installed in the test env

**Probe** (substitute the literal interpreter selected in Check 1):
```bash
test -x "<test-python>" &&
  "<test-python>" -B -c "from importlib.metadata import version; print(version('pytest-httpchain'))"
```
Run it as written, changing only the interpreter path, subject to the host client's approval policy. Pass = it prints a version; retain it for the version gates below. This confirms the distribution is installed in the selected test environment; it does not run pytest or import the user's fixtures. If the interpreter is missing, report that the user must create/sync the test environment themselves before re-running.

**Remediation (not installed), printed for the detected manager:**
> pytest-httpchain isn't installed in this project's test environment. Add it as a dev dependency, then re-run:
> - uv:     `uv add --group dev pytest-httpchain`
> - poetry: `poetry add --group dev pytest-httpchain`
> - pdm:    `pdm add -dG dev pytest-httpchain`
> - pip:    `pip install pytest-httpchain` (and record it in your dev requirements)

## Check 3 — scenarios discoverable

**Probe.**
1. **Find the pytest config table pytest actually reads.** In the project root, the first of these that holds pytest config wins, and pytest ignores the rest:
   `pytest.toml` / `.pytest.toml` (`[pytest]`) → `pytest.ini` / `.pytest.ini` (`[pytest]`) → `pyproject.toml` (`[tool.pytest]`, pytest 9's native table, **or** `[tool.pytest.ini_options]` — never both: pytest 9 refuses to start) → `tox.ini` (`[pytest]`) → `setup.cfg` (`[tool:pytest]`).
2. Read `httpchain_suffix` from that table (default `http`; the name needs pytest-httpchain ≥0.10). For older packages read `suffix`; 0.10 still honors that deprecated alias when `httpchain_suffix` is absent.
3. **Doctor, pytest-httpchain ≥0.16:** let the package discover and shallow-validate the project in one call, so its supported extensions and directory exclusions stay authoritative:
   ```bash
   "<test-python>" -B -m pytest_httpchain.cli validate --format json .
   ```
   This includes both `test_*.<suffix>.json` and `test_*.<suffix>.jsonc`. Parse `files` and retain the response for Check 4; count actual scenario entries. A sole directory result whose only diagnostic is `HTTPCHAIN039` means zero scenarios: report the empty suite and skip Check 4, despite the CLI's nonzero exit. Any other failure (including unreadable/invalid config, malformed output, or failure to launch) is a failure, never an empty suite.
4. **Doctor, package <0.16:** directory validation and JSONC collection are unavailable. Find `test_*.<suffix>.json` files under the project (typically `tests/`), respecting pytest's default directory exclusions, existing virtual environments and symlinked directories; pass those literal file paths to Check 4. If matching `.jsonc` files exist, explain that this installed version does not collect them and suggest upgrading; do not rename them.

Report the installed version, suffix, where it came from, supported extension(s), and count. **Zero is not a failure** — it's an empty suite; say so. Setup only reads the config in steps 1–2; it never performs doctor validation.

With package ≥0.11, **FAIL** if that table still has a bare `suffix` key (the pre-0.10 name). Count against the suffix pytest really uses (`httpchain_suffix`, else `http`). On 0.10 report the alias as deprecated; on older packages it is the supported spelling.

**Remediation (stale `suffix` key):**
> Your pytest config sets `suffix`, an option name pytest-httpchain no longer reads: since 0.11 pytest warns `Unknown config option: suffix` (and aborts under `--strict-config` / `strict = true`), and scenarios are discovered with `httpchain_suffix` (default `http`) instead. Rename the key to `httpchain_suffix` in the same table, or run `pytest-httpchain-setup`.

**Remediation (informational, when zero found):**
> No collected scenarios found (suffix = `<suffix>`; extensions = `<supported-extensions>`). That's fine for a fresh setup — write one (the `pytest-httpchain` skill covers the format) or run `pytest-httpchain-setup` to scaffold an example.

## Check 4 — scenarios validate clean

**Probe** (SKIP if Check 2 failed or Check 3 found zero). For package ≥0.16, reuse Check 3's directory-validation response; do not validate the same files twice. For older packages, shallow-validate the discovered literal JSON file paths:
```bash
"<test-python>" -B -m pytest_httpchain.cli validate --format json <scenario> [<scenario> ...]
```
Pass = exit 0 and every file `valid`. Parse the JSON; surface any `HTTPCHAINxxx` diagnostics (code + severity + message) per file.

**Remediation (one or more invalid):**
> `<N>` scenario(s) failed validation — see the `HTTPCHAINxxx` diagnostics above. Fix each error and re-run, or use the shared authoring procedure in `skills/pytest-httpchain/references/authoring.md` to repair it (inline or delegated). Run `--deep --syspath <dir>` to additionally check that `module:func` references resolve.
