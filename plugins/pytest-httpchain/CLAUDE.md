# CLAUDE.md

Developer notes for working inside `pytest-httpchain` (the plugin). User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## Shared client support

This plugin supports Claude Code and Codex. `plugin.json` is the portable Codex manifest; `.claude-plugin/plugin.json` is the Claude manifest. Both catalogs and both manifests must agree on name, description and version. Setup/doctor procedures live in the named skills; `commands/` contains Claude wrappers. Keep changes to the shared procedures in `skills/`, not in those wrappers.

## What this plugin is

A **companion** to the [`pytest-httpchain`](https://github.com/aeresov/pytest-httpchain) pytest plugin (a separate repo / PyPI package). It doesn't reimplement the package — the package owns the CLI (`validate` plus the read-only `schema`/`resolve`/`show`/`graph` inspection commands, ≥0.5.0), the pytest collection, and the `HTTPCHAINxxx` diagnostics. This plugin owns the assistant workflow: the scenario-authoring skill, a static-validating subagent, and `/setup` + `/doctor`. No MCP server; no bundled package code (it only *invokes* the package via the project's runner).

## Design decisions

Folded in here in lieu of a separate spec:
- **Companion, not mirror.** Adds workflow around the package; never duplicates its CLI or collection logic.
- **The authoring skill lives here, and only here.** It used to ship in the package (`src/pytest_httpchain/skill.md`, placed by a `pytest-httpchain install` command). Both that command and that file were **removed from the package**; the skill now lives solely at `skills/pytest-httpchain/SKILL.md`. Single source, no sync. Change scenario syntax in the package → update this skill (nothing enforces it — see Gotchas).
- **One authoring skill.** No competing package-installed skill anymore, so no router/authoring split: one authoring skill carries the reference plus a short workflow section; separate shared setup/doctor skills handle configuration and health checks. The authoring preamble owns the setup check, inline-vs-subagent dispatch and always-validate rule.
- **Author + static-validate only.** Neither skill nor subagent makes a live HTTP call — a scenario routinely contains `POST`/`PUT`/`DELETE`, so firing it is the user's call, made by running `pytest`. The subagent runs only the read-only `pytest-httpchain` subcommands (`validate`, `resolve`, `show`, `graph`, `schema`).
- **Conservative `/setup`.** Guides the dependency install (prints the per-manager command) but never runs it; writes only files the user approves. Mirrors `mysql-client`'s non-privileged setup.

## Layout
- `skills/pytest-httpchain/SKILL.md` — the authoring skill. Frontmatter `description` is load-bearing (it decides triggering). Body: a short workflow preamble (setup check; inline vs subagent; always validate), the core authoring reference (scenario/stage/request/response/templates), a `## References` index, then validate + the read-only inspection commands (`resolve`/`show`/`graph`/`schema`). The canonical authoring guide — keep it accurate to the package's models.
- `skills/pytest-httpchain/references/` — loaded on demand, matching the other plugins' progressive-disclosure shape.
  - `composition.md` — substitutions, `$include`/`$merge`/`$ref`, parametrize, parallel execution.
  - `example.md` — one complete multi-stage scenario, end to end.
- `agents/httpchain-author.md` — Claude wrapper around `skills/pytest-httpchain/references/authoring.md`. Codex uses that same procedure through its available subagents or inline. The procedure refuses live HTTP, reads the authoring skill, and validates statically.
- `commands/setup.md` — `/pytest-httpchain:setup`: detect manager → verify/guide install → optional `httpchain_suffix` ini (written into the pytest config table the project already uses — never a second one) → optional scaffold. Never installs, validates, or dispatches.
- `commands/doctor.md` — `/pytest-httpchain:doctor`: runs checks 1–4 read-only (including a static `validate` pass). Reports; fixes nothing.
- `setup-checklist.md` — the four shared checks + verbatim remediation, read by both commands so they can't drift. Plugin-root file, deliberately not a slash command.

## Validation

```bash
claude plugin validate .                        # marketplace manifest (from repo root)
claude plugin validate plugins/pytest-httpchain # this plugin's manifest
python3 -m unittest discover -s scripts/tests -p test_httpchain_workflow.py
# Optional package contract, using an already-installed >=0.16 test environment:
HTTPCHAIN_TEST_PYTHON=/absolute/test/env/bin/python python3 -m unittest discover -s scripts/tests -p test_httpchain_workflow.py
```

The plugin ships no Python runtime, so the marketplace CI's per-project test step finds nothing here. Repository-level workflow recipe tests cover probes; an optional offline package contract check also covers directory/JSONC validation. To smoke-test behavior, install it into a uv project that has `pytest-httpchain` in its existing test environment, run `/pytest-httpchain:doctor`, then ask Claude to author a scenario and confirm it validates.

## Gotchas
- **The skill is the only copy of the authoring guide.** When the package changes scenario syntax (new body type, new response step, renamed field, new `HTTPCHAINxxx` code), this `SKILL.md` (and `references/composition.md`) won't update itself. Treat a package syntax change as a paired edit here, and bump the plugin version. Source of truth (single distribution since 0.9.0, under `src/pytest_httpchain/`): `models/entities.py` + `models/types.py` (scenario models), `validation/diagnostics.py` (`HTTPCHAINxxx` codes + the `SEVERITY` map; table in `docs/diagnostics.md`) with `validation/semantic.py`/`deep.py`, `templates/substitution.py` (template built-ins), `plugin.py` + `constants.py` (ini options), `cli.py` (CLI), and `CHANGELOG.md`.
- **`HTTPCHAINxxx` severities are load-bearing.** Errors (fail `validate`): `000`–`002`, `007`, `008`, `010`–`012`, `014`–`017`, `019`. Warnings (pass `validate` unless `--strict`): `003`–`006`, `009`, `013`, `018`, `020`–`024` (the `--deep` checks — always warnings), `026`–`030`. Info (never affects validity, exempt from `--strict`): `025`. As of 0.15.0; re-check `SEVERITY` when the package adds a code. Don't describe a warning code as an error — `doctor.md`'s sample output, the skill, and the agent's stop rule depend on getting this right.
- **The no-live-HTTP boundary must reach the executing context.** It is in the main skill and shared `references/authoring.md` procedure; the Claude wrapper and Codex delegate must read the procedure before authoring.
- **The skill `description` is load-bearing.** Keep the concrete trigger phrases (`test_<name>.http.json`, "scenario", "`$ref`/`$include`", "`HTTPCHAINxxx`", "chain HTTP calls"). Dropping them narrows triggering; adding unrelated phrases broadens it past the plugin's competence.
- **Use the existing test interpreter directly**, selected by `setup-checklist.md`: `"<test-python>" -B -m pytest_httpchain.cli …`. Package-manager launchers can create environments or synchronize dependencies even for a version probe (`uv run --no-sync` still creates a missing venv). Retain the literal venv interpreter path across delegated work. `--deep` imports the user's `module:func` code, so it must run in that environment — never `uvx`. Directory/JSONC validation needs package ≥0.16; older versions use explicit JSON paths. Doctor reuses the directory result and treats only a sole `HTTPCHAIN039` result as an empty suite.
- **Version bumps:** update both marketplace catalogs, both plugin manifests and any in-tree Python project/lockfile. `scripts/check-version-sync.sh` checks metadata and package paths; `uv sync --locked` checks Python lock consistency.
