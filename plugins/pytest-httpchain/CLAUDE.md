# CLAUDE.md

Developer notes for working inside `pytest-httpchain` (the plugin). User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## Shared client support

This plugin supports Claude Code and Codex. `plugin.json` is the portable Codex manifest; `.claude-plugin/plugin.json` is the Claude manifest. Both catalogs and both manifests must agree on name, description and version. Setup/doctor procedures live in the named skills; `commands/` contains Claude wrappers. Keep changes to the shared procedures in `skills/`, not in those wrappers.

## What this plugin is

A **companion** to the [`pytest-httpchain`](https://github.com/aeresov/pytest-httpchain) pytest plugin (a separate repo / PyPI package). It doesn't reimplement the package — the package owns the CLI (`validate`, the read-only `schema`/`resolve`/`show`/`graph` inspection commands, ≥0.5.0, and `import har|curl`, ≥0.16), the pytest collection, and the `HTTPCHAINxxx` diagnostics. This plugin owns the assistant workflow: the scenario-authoring skill, a static-validating subagent, and `/setup` + `/doctor`. No MCP server; no bundled package code (it only *invokes* the package via the project's runner).

## Design decisions

Folded in here in lieu of a separate spec:
- **Companion, not mirror.** Adds workflow around the package; never duplicates its CLI or collection logic.
- **The authoring skill lives here, and only here.** It used to ship in the package (`src/pytest_httpchain/skill.md`, placed by a `pytest-httpchain install` command). Both that command and that file were **removed from the package**; the skill now lives solely at `skills/pytest-httpchain/SKILL.md`. Single source, no sync. Change scenario syntax in the package → update this skill (only the opt-in documentation contract checks it — see Validation and Gotchas).
- **One authoring skill.** No competing package-installed skill anymore, so no router/authoring split: one authoring skill carries the reference plus a short workflow section; separate shared setup/doctor skills handle configuration and health checks. The authoring preamble owns the setup check, inline-vs-subagent dispatch and always-validate rule.
- **Author + static-validate only.** Neither skill nor subagent makes a live HTTP call or opens a connection — a scenario routinely contains `POST`/`PUT`/`DELETE`, so firing it is the user's call, made by running `pytest`. The subagent runs only the static `pytest-httpchain` subcommands (`validate`, `resolve`, `show`, `graph`, `schema`, `import`).
- **`import` is in; executing what it imports is out.** `import har|curl` sends nothing and only writes the scenario the author would write anyway, so it sits inside the boundary. A curl command is the user's text (quotes, `$(...)`, a token): the skill has it written to a file and fed on stdin, never pasted onto a shell line, and never run as curl.
- **One reference, gated by the installed version.** The skill documents one package release (the `documents pytest-httpchain **X.Y.Z**` line in `SKILL.md`, which the contract tests read) and carries a *Version gates* table, so a project on an older package gets scenarios that version can load. It does not fork into per-version references.
- **The project's Python stays the project's.** User functions and connection adapters are named by a scenario, not written by the author unless the caller asks; a missing one is an open question, and its `--deep` warning is reported, not hidden.
- **Conservative `/setup`.** Guides the dependency install (prints the per-manager command) but never runs it; writes only files the user approves. Mirrors `mysql-client`'s non-privileged setup.

## Layout
- `skills/pytest-httpchain/SKILL.md` — the authoring skill. Frontmatter `description` is load-bearing (it decides triggering). Body: a short workflow preamble (setup check; inline vs subagent; always validate), the *Version gates* table, the core authoring reference (scenario/stage/request/auth/response/templates), a `## References` index, then validate, the read-only inspection commands (`resolve`/`show`/`graph`/`schema`) and `import`. The canonical authoring guide — keep it accurate to the package's models, and keep it the core: detail goes to a reference.
- `skills/pytest-httpchain/references/` — loaded on demand, matching the other plugins' progressive-disclosure shape.
  - `composition.md` — substitutions, `$include`/`$merge`/`$ref`, parametrize, parallel execution (collected saves, thresholds, stats).
  - `flow-control.md` — `retry`/polling, `skip_if`, `always_run`, marks that skip, pytest-xdist rules.
  - `templates.md` — every template built-in, reading objects, literal braces, what the engine refuses, `null` rendering.
  - `connections.md` — connection stages (non-HTTP protocols) and the adapter contract.
  - `diagnostics.md` — every `HTTPCHAINxxx` code with severity and fix. **The one place severities are listed.**
  - `example.md` — one complete multi-stage scenario, end to end.
  - `authoring.md` — the shared multi-step authoring procedure (below).
- `agents/httpchain-author.md` — Claude wrapper around `skills/pytest-httpchain/references/authoring.md`. Codex uses that same procedure through its available subagents or inline. The procedure refuses live HTTP, reads the authoring skill, and validates statically.
- `commands/setup.md` — `/pytest-httpchain:setup`: detect manager → verify/guide install → optional `httpchain_suffix` ini (written into the pytest config table the project already uses — never a second one) → optional scaffold. Never installs, validates, or dispatches.
- `commands/doctor.md` — `/pytest-httpchain:doctor`: runs checks 1–4 read-only (including a static `validate` pass). Reports; fixes nothing.
- `setup-checklist.md` — the four shared checks + verbatim remediation, read by both commands so they can't drift. Plugin-root file, deliberately not a slash command.

## Validation

```bash
claude plugin validate .                        # marketplace manifest (from repo root)
claude plugin validate plugins/pytest-httpchain # this plugin's manifest
python3 -m unittest discover -s scripts/tests -p test_httpchain_workflow.py
# Optional package contract, using an already-installed test environment (>=0.16 for the
# checklist recipes; the release SKILL.md documents, or newer, for the reference itself):
HTTPCHAIN_TEST_PYTHON=/absolute/test/env/bin/python python3 -m unittest discover -s scripts/tests -p test_httpchain_workflow.py
```

The plugin ships no Python runtime, so the marketplace CI's per-project test step finds nothing here. Repository-level workflow recipe tests cover probes; the optional offline package contract (skipped in CI, which sets no `HTTPCHAIN_TEST_PYTHON`) covers two things:

- **Checklist recipes** — directory/JSONC validation and the empty-suite `HTTPCHAIN039` result.
- **The reference against the package** (`DocumentationContractTests`) — every fenced ```` ```json ```` block under `skills/` that is a whole scenario (parses as JSON, has `stages`, uses no `$include`/`$merge`/`$ref`) must validate with **no** error or warning; the severity column of `references/diagnostics.md` must equal the package's `SEVERITY` map; and every name in the package's `TEMPLATE_BUILTINS` must appear in `references/templates.md`. Run it whenever the reference or the documented release changes: `uv venv <dir> && uv pip install --python <dir>/bin/python pytest-httpchain==<release>` gives a throwaway interpreter.

So write a new example as a whole, warning-free scenario where that is practical — it is then checked for free — and keep deliberate fragments as fragments. To smoke-test behavior, install the plugin into a uv project that has `pytest-httpchain` in its existing test environment, run `/pytest-httpchain:doctor`, then ask Claude to author a scenario and confirm it validates.

## Gotchas
- **The skill is the only copy of the authoring guide.** When the package changes scenario syntax (new body type, new response step, renamed field, new `HTTPCHAINxxx` code), `SKILL.md` and its `references/` won't update themselves. Treat a package release as a paired edit here, and bump the plugin version. The routine: read the release's `CHANGELOG.md` section; update the reference; add a row to *Version gates* for anything a scenario can now write; move the `documents pytest-httpchain **X.Y.Z**` line; run the documentation contract against that release (see Validation). Source of truth (single distribution since 0.9.0, under `src/pytest_httpchain/`): `models/entities.py` + `models/types.py` (scenario models), `validation/diagnostics.py` (`HTTPCHAINxxx` codes + the `SEVERITY` map; table in `docs/diagnostics.md`) with `validation/semantic.py`/`deep.py`, `templates/substitution.py` + `templates/functions.py` (template built-ins), `plugin.py` + `constants.py` (ini options), `cli.py` (CLI), `docs/` (the user guide the reference condenses), and `CHANGELOG.md`. `pytest-httpchain schema` from each release is the quickest way to see which keys a version gained.
- **`HTTPCHAINxxx` severities are load-bearing.** They are listed once, in `references/diagnostics.md`, and the contract test compares that table with the package's `SEVERITY`. As of 0.19.0 — errors (fail `validate`): `000`–`002`, `007`, `008`, `010`–`012`, `014`–`017`, `019`, `031`–`034`, `038`, `039`, `041`–`044`. Warnings (pass `validate` unless `--strict`): `003`–`006`, `009`, `013`, `018`, `020`–`024` and `046` (the `--deep` checks — always warnings), `026`–`030`, `035`–`037`, `040`, `045`. Info (never affects validity, exempt from `--strict`): `025`. Don't describe a warning code as an error — the doctor skill's sample output, the authoring skill, and the agent's stop rule depend on getting this right. Mind the pairs that differ only by where they occur: `037` (warning, in a stage) vs `038` (error, before any stage runs).
- **A newer scenario on an older package fails as `HTTPCHAIN000`** ("Extra inputs are not permitted" for `client`, `retry`, `connection`, …), dozens of lines of it. That is a version mismatch, not a broken scenario: the checklist's check 4 remediation says to upgrade, and the skill's *Version gates* keep the author from causing it.
- **The no-live-HTTP boundary must reach the executing context.** It is in the main skill and shared `references/authoring.md` procedure; the Claude wrapper and Codex delegate must read the procedure before authoring. It covers connection stages (no socket is opened) and `import curl` (the command is parsed, never run).
- **The skill `description` is load-bearing.** Keep the concrete trigger phrases (`test_*.http.json`, "scenario", "`$ref`/`$include`", "`HTTPCHAINxxx`", "chain HTTP calls", "curl command or HAR recording"). Dropping them narrows triggering; adding unrelated phrases broadens it past the plugin's competence. It is one YAML plain scalar: no `: ` and no ` #` inside it.
- **Use the existing test interpreter directly**, selected by `setup-checklist.md`: `"<test-python>" -B -m pytest_httpchain.cli …`. Package-manager launchers can create environments or synchronize dependencies even for a version probe (`uv run --no-sync` still creates a missing venv). Retain the literal venv interpreter path across delegated work. `--deep` imports the user's `module:func` code, so it must run in that environment — never `uvx`. Directory/JSONC validation needs package ≥0.16; older versions use explicit JSON paths. Doctor reuses the directory result and treats only a sole `HTTPCHAIN039` result as an empty suite.
- **Version bumps:** update both marketplace catalogs, both plugin manifests and any in-tree Python project/lockfile. `scripts/check-version-sync.sh` checks metadata and package paths; `uv sync --locked` checks Python lock consistency.
