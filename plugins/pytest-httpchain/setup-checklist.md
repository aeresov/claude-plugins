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
Run it as written, changing only the interpreter path, subject to the host client's approval policy. Pass = it prints a version from 0.4.0 on (below); retain it for the version gates below. This confirms the distribution is installed in the selected test environment; it does not run pytest or import the user's fixtures. If the interpreter is missing, report that the user must create/sync the test environment themselves before re-running.

Any installed version from 0.4.0 passes. An older one is a **FAIL**: `validate` arrived in 0.3.0, and `--format json`, `--deep`, `--syspath`, `--strict` and the `HTTPCHAINxxx` codes this workflow reads in 0.4.0. When a passing version is older than the release `skills/pytest-httpchain/SKILL.md` says it documents, add one line to the report naming both versions: scenario features above the installed version (that skill's *Version gates*) are unavailable until the user upgrades.

**Remediation (not installed), printed for the detected manager:**
> pytest-httpchain isn't installed in this project's test environment. Add it as a dev dependency, then re-run:
> - uv:     `uv add --group dev pytest-httpchain`
> - poetry: `poetry add --group dev pytest-httpchain`
> - pdm:    `pdm add -dG dev pytest-httpchain`
> - pip:    `pip install pytest-httpchain` (and record it in your dev requirements)

**Remediation (older than 0.4.0), printed for the detected manager, `<documented>` being the release `skills/pytest-httpchain/SKILL.md` documents:**
> pytest-httpchain `<version>` predates 0.4.0, the first release with the `validate --format json` output and `HTTPCHAINxxx` codes this workflow reads. Upgrade the dev dependency, then re-run:
> - uv:     `uv add --group dev "pytest-httpchain>=<documented>"`
> - poetry: `poetry add --group dev "pytest-httpchain>=<documented>"`
> - pdm:    `pdm add -dG dev "pytest-httpchain>=<documented>"`
> - pip:    `pip install "pytest-httpchain>=<documented>"` (and record it in your dev requirements)

## Check 3 — scenarios discoverable

**Probe.**
1. **Find the pytest config file and table pytest actually reads.** In the project root, pytest takes the first of these it finds and ignores the rest:
   `pytest.toml` / `.pytest.toml` (`[pytest]`) → `pytest.ini` / `.pytest.ini` (`[pytest]`) → `pyproject.toml` (`[tool.pytest]`, pytest 9's native table, **or** `[tool.pytest.ini_options]` — never both: pytest 9 refuses to start) → `tox.ini` (`[pytest]`) → `setup.cfg` (`[tool:pytest]`).
   `pytest.toml`, `.pytest.toml`, `pytest.ini` and `.pytest.ini` win as soon as the file exists, even empty or without its `[pytest]` table/section, and then set nothing (suffix `http`). `pyproject.toml`, `tox.ini` and `setup.cfg` count only when they hold their table.
2. Read `httpchain_suffix` from that table (default `http`; the name needs pytest-httpchain ≥0.10). For older packages read `suffix`; 0.10 still honors that deprecated alias when `httpchain_suffix` is absent. Below 0.21, also read `httpchain_ref_parent_traversal_depth` (`ref_parent_traversal_depth` before 0.10, which 0.10 still honors when the new name is absent): when it is set, add `--ref-parent-traversal-depth <N>` with its value to every `validate` command below. Those releases' CLI does not read the option and allows 3, so a deeper reference that collection resolves fails as `HTTPCHAIN012`; from 0.21 it reads the option itself.
3. **Doctor, pytest-httpchain ≥0.16:** let the package discover and shallow-validate the project in one call, so its supported extensions and pytest's default directory exclusions apply:
   ```bash
   "<test-python>" -B -m pytest_httpchain.cli validate --format json .
   ```
   This includes both `test_*.<suffix>.json` and `test_*.<suffix>.jsonc`. Unlike pytest, it enters no symlinked subdirectory and reads none of the project's `testpaths`, `norecursedirs`, `--ignore` or `collect_ignore`. So name the config's `testpaths` directories (globs expanded), when it sets them, in place of `.`, and also name each symlinked directory under the searched paths that holds scenarios. A file that only the project's own exclusions keep from pytest is still validated: report it, noting that pytest does not collect it. Parse `files` and retain the response for Check 4; count actual scenario entries. A directory entry whose only diagnostic is `HTTPCHAIN039` holds no scenarios and is no failure, though it makes the exit nonzero; when every entry is one, report the empty suite and skip Check 4. Any other failure (including unreadable/invalid config, malformed output, or failure to launch) is a failure, never an empty suite.
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
Pass = every scenario entry `valid`, and exit 0 unless only `HTTPCHAIN039` directory entries made it nonzero. Parse the JSON; surface any `HTTPCHAINxxx` diagnostics (code + severity + message) per file.

**Remediation (one or more invalid):**
> `<N>` scenario(s) failed validation — see the `HTTPCHAINxxx` diagnostics above (`skills/pytest-httpchain/references/diagnostics.md` has each code's fix). Fix each error and re-run, or use the shared authoring procedure in `skills/pytest-httpchain/references/authoring.md` to repair it (inline or delegated). An `HTTPCHAIN000` naming a key as not permitted (`client`, `retry`, `connection`, …) usually means the scenario uses a feature newer than the installed pytest-httpchain: upgrade the package rather than rewrite the scenario. Run `--deep --syspath <dir>` to additionally check that `module:func` references and connection adapters resolve.
