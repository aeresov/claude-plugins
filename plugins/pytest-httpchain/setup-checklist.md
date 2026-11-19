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
Run it as written, changing only the interpreter path, subject to the host client's approval policy. Pass = it prints a version from 0.16.1 on (below); retain it for the version gates below. This confirms the distribution is installed in the selected test environment; it does not run pytest or import the user's fixtures. If the interpreter is missing, report that the user must create/sync the test environment themselves before re-running.

Any installed version from 0.16.1 passes. An older one is a **FAIL**: the authoring skill and these checks take for granted what arrived by 0.16.1 — `.jsonc` scenarios and `validate <directory>`, the `client` block, built-in `auth`, `verify.jmespath`, `skip_if`, `retry`, `import`, and a CLI that resolves `$ref`s from pytest's rootdir, as collection does (every release before 0.9.0 is also yanked from PyPI). When a passing version is older than the release `skills/pytest-httpchain/SKILL.md` says it documents, add one line to the report naming both versions: scenario features above the installed version (that skill's *Version gates*) are unavailable until the user upgrades.

**Remediation (not installed), printed for the detected manager:**
> pytest-httpchain isn't installed in this project's test environment. Add it as a dev dependency, then re-run:
> - uv:     `uv add --group dev pytest-httpchain`
> - poetry: `poetry add --group dev pytest-httpchain`
> - pdm:    `pdm add -dG dev pytest-httpchain`
> - pip:    `pip install pytest-httpchain` (and record it in your dev requirements)

**Remediation (older than 0.16.1), printed for the detected manager, `<documented>` being the release `skills/pytest-httpchain/SKILL.md` documents:**
> pytest-httpchain `<version>` predates 0.16.1, the oldest release this workflow supports. Upgrade the dev dependency, then re-run:
> - uv:     `uv add --group dev "pytest-httpchain>=<documented>"`
> - poetry: `poetry add --group dev "pytest-httpchain>=<documented>"`
> - pdm:    `pdm add -dG dev "pytest-httpchain>=<documented>"`
> - pip:    `pip install "pytest-httpchain>=<documented>"` (and record it in your dev requirements)

## Check 3 — scenarios discoverable

**Probe.**
1. **Find the pytest config file and table pytest actually reads.** In the project root, then in each parent directory in turn, pytest takes the first of these that counts (below) and ignores the rest:
   `pytest.toml` / `.pytest.toml` (`[pytest]`) → `pytest.ini` / `.pytest.ini` (`[pytest]`) → `pyproject.toml` (`[tool.pytest]`, pytest 9's native table, **or** `[tool.pytest.ini_options]` — never both: pytest 9 refuses to start) → `tox.ini` (`[pytest]`) → `setup.cfg` (`[tool:pytest]`).
   That is pytest 9's order, which every supported release requires.
   `pytest.toml`, `.pytest.toml`, `pytest.ini` and `.pytest.ini` count as soon as the file exists, even empty or without its `[pytest]` table/section, and then set nothing (suffix `http`). A `pytest.toml`/`.pytest.toml` with options outside any table counts too, and stops the search, but pytest never reads those options: 9.0 and 9.1 read only its `[pytest]` table (none: the file sets nothing), and 9.2 and later refuse to start on it when it has none. `validate` reads only that table and passes, so report the file as a check 3 **FAIL**. `pyproject.toml`, `tox.ini` and `setup.cfg` count only when they hold their table. A `pyproject.toml` without its table does not stop the search: the nearest one is the config file, setting nothing, only when no directory up to the filesystem root holds a file that counts. A file found in a parent directory (a monorepo's) is the project's config file, and its directory is pytest's rootdir: report its path.
2. Read `httpchain_suffix` from that table (default `http`). Below 0.21, also read `httpchain_ref_parent_traversal_depth`: when it is set, add `--ref-parent-traversal-depth <N>` with its value to the `validate` command below. Those releases' CLI does not read the option and allows 3, so a deeper reference that collection resolves fails as `HTTPCHAIN012`; from 0.21 it reads the option itself.
3. **Doctor:** let the package discover and shallow-validate the project in one call, so its supported extensions and pytest's default directory exclusions apply:
   ```bash
   "<test-python>" -B -m pytest_httpchain.cli validate --format json .
   ```
   This includes both `test_*.<suffix>.json` and `test_*.<suffix>.jsonc`. Unlike pytest, it enters no symlinked subdirectory and reads none of the project's `testpaths`, `norecursedirs`, `--ignore` or `collect_ignore`. So, when the config file is in the project root and sets `testpaths` (pytest reads them only when run from its rootdir with no path arguments), expand each entry as a glob, as pytest does, and name in place of `.` the directories they match and any matched file named `test_*.<suffix>.json` or `.jsonc`. Leave out every other matched file (pytest collects no scenario from it, and `validate` reads every file it is named as a scenario, so a `conftest.py` fails as `HTTPCHAIN014`) and an entry that matches nothing, as pytest does (`validate` reports a named missing path as an `HTTPCHAIN010` error). When no entry matches anything, pytest searches `.`, so keep `.`; when they match only files left out, pytest collects no scenario: report the empty suite and skip Check 4. Also name each symlinked directory under the searched paths that holds scenarios. A file that only the project's own exclusions keep from pytest is still validated: report it, noting that pytest does not collect it. Parse `files` and retain the response for Check 4; count actual scenario entries. A directory entry whose only diagnostic is `HTTPCHAIN039` holds no scenarios and is no failure, though it makes the exit nonzero; when every entry is one, report the empty suite and skip Check 4. Any other failure (including unreadable/invalid config, malformed output, or failure to launch) is a failure, never an empty suite.

Report the installed version, suffix, where it came from, supported extension(s), and count. **Zero is not a failure** — it's an empty suite; say so. Setup only reads the config in steps 1–2; it never performs doctor validation.

**FAIL** if that table still has a bare `suffix` key (the pre-0.10 name, ignored since 0.11). Count against the suffix pytest really uses (`httpchain_suffix`, else `http`).

**Remediation (stale `suffix` key):**
> Your pytest config sets `suffix`, an option name pytest-httpchain no longer reads: since 0.11 pytest warns `Unknown config option: suffix` (and aborts under `--strict-config` / `strict = true`), and scenarios are discovered with `httpchain_suffix` (default `http`) instead. Rename the key to `httpchain_suffix` in the same table, or run `pytest-httpchain-setup`.

**Remediation (options outside `[pytest]`):**
> `<file>` sets options outside any table, which pytest never reads (pytest 9.2 and later refuse to start on it when it has no `[pytest]` table). Move them under its `[pytest]` table, each in TOML's own type, then re-run: there pytest checks the type and rejects a quoted value where it expects a list (`addopts = ["-q"]`, `testpaths = ["tests"]`), an integer (`httpchain_ref_parent_traversal_depth = 5`) or `true`/`false`.

**Remediation (informational, when zero found):**
> No collected scenarios found (suffix = `<suffix>`; extensions = `<supported-extensions>`). That's fine for a fresh setup — write one (the `pytest-httpchain` skill covers the format) or run `pytest-httpchain-setup` to scaffold an example.

## Check 4 — scenarios validate clean

**Probe** (SKIP if Check 2 failed or Check 3 found zero). Reuse Check 3's directory-validation response; do not validate the same files twice.
Pass = every scenario entry `valid`, and exit 0 unless only `HTTPCHAIN039` directory entries made it nonzero. Parse the JSON; surface any `HTTPCHAINxxx` diagnostics (code + severity + message) per file.

**Remediation (one or more invalid):**
> `<N>` scenario(s) failed validation — see the `HTTPCHAINxxx` diagnostics above (`skills/pytest-httpchain/references/diagnostics.md` has each code's fix). Fix each error and re-run, or use the shared authoring procedure in `skills/pytest-httpchain/references/authoring.md` to repair it (inline or delegated). An `HTTPCHAIN000` naming a key as not permitted (`response_codec`, `connections`, `operation_name`, …) usually means the scenario uses a feature newer than the installed pytest-httpchain: upgrade the package rather than rewrite the scenario. Run `--deep --syspath <dir>` to additionally check that `module:func` references and connection adapters resolve.
