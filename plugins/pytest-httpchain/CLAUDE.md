# CLAUDE.md

Developer notes for working inside `pytest-httpchain` (the plugin). User-facing docs: [`README.md`](README.md). Marketplace-wide conventions: repo-root `CLAUDE.md`.

## What this plugin is

A **companion** to the [`pytest-httpchain`](https://github.com/aeresov/pytest-httpchain) pytest plugin (a separate repo / PyPI package). It doesn't reimplement the package — the package owns the CLI (`validate` plus the read-only `schema`/`resolve`/`show`/`graph` inspection commands, ≥0.5.0), the pytest collection, and the `HTTPCHAINxxx` diagnostics. This plugin owns the Claude Code side: the scenario-authoring skill, a static-validating subagent, and `/setup` + `/doctor`. No MCP server; no bundled package code (it only *invokes* the package via the project's runner).

## Design decisions

Folded in here in lieu of a separate spec:

- **Companion, not mirror.** Adds workflow around the package; never duplicates its CLI or collection logic.
- **The authoring skill lives here, and only here.** It used to ship in the package (`src/pytest_httpchain/skill.md`, placed by a `pytest-httpchain install` command). Both that command and that file were **removed from the package**; the skill now lives solely at `skills/pytest-httpchain/SKILL.md`. Single source, no sync. Change scenario syntax in the package → update this skill (nothing enforces it — see Gotchas).
- **One skill, not two.** No competing package-installed skill anymore, so no router/authoring split: a single skill carries the authoring reference plus a short workflow section (setup check, inline-vs-subagent dispatch, always-validate).
- **Author + static-validate only.** Neither skill nor subagent makes a live HTTP call — a scenario routinely contains `POST`/`PUT`/`DELETE`, so firing it is the user's call, made by running `pytest`. The subagent's only command is `pytest-httpchain validate`.
- **Conservative `/setup`.** Guides the dependency install (prints the per-manager command) but never runs it; writes only files the user approves. Mirrors `mysql-client`'s non-privileged setup.

## Layout

- `skills/pytest-httpchain/SKILL.md` — the one skill. Frontmatter `description` is load-bearing (it decides triggering). Body: a short workflow preamble (setup check; inline vs subagent; always validate), the core authoring reference (scenario/stage/request/response/templates), a `## References` index, then validate + the read-only inspection commands (`resolve`/`show`/`graph`/`schema`). The canonical authoring guide — keep it accurate to the package's models.
- `skills/pytest-httpchain/references/` — loaded on demand, matching the other plugins' progressive-disclosure shape.
  - `composition.md` — substitutions, `$include`/`$merge`/`$ref`, parametrize, parallel execution.
  - `example.md` — one complete multi-stage scenario, end to end.
- `agents/httpchain-author.md` — context-isolated authoring subagent. Tools `Write, Edit, Read, Bash, Grep, Glob`; `model: sonnet`. Reads the SKILL.md (and its `references/`) as its reference, writes `test_*.http.json`, loops on `validate`, and **refuses live HTTP** (the no-network rule is stated in the agent file, not just the skill — subagents don't inherit the parent's skill context).
- `commands/setup.md` — `/pytest-httpchain:setup`: detect manager → verify/guide install → optional `suffix` ini → optional scaffold. Never installs, validates, or dispatches.
- `commands/doctor.md` — `/pytest-httpchain:doctor`: runs checks 1–4 read-only (including a static `validate` pass). Reports; fixes nothing.
- `setup-checklist.md` — the four shared checks + verbatim remediation, read by both commands so they can't drift. Plugin-root file, deliberately not a slash command.

## Validation

```bash
claude plugin validate .                        # marketplace manifest (from repo root)
claude plugin validate plugins/pytest-httpchain # this plugin's manifest
```

The plugin ships no Python, so the marketplace CI's test-suite step finds nothing here — only the manifest checks apply. To smoke-test behavior, install it into a uv project that has `pytest-httpchain` as a dev dependency, run `/pytest-httpchain:doctor`, then ask Claude to author a scenario and confirm it validates.

## Gotchas

- **The skill is the only copy of the authoring guide.** When the package changes scenario syntax (new body type, new response step, renamed field, new `HTTPCHAINxxx` code), this `SKILL.md` (and `references/composition.md`) won't update itself. Treat a package syntax change as a paired edit here, and bump the plugin version. Source of truth: the package's `packages/pytest-httpchain-models` and `validation.py`.
- **`HTTPCHAINxxx` severities are load-bearing.** `000/001/002/007/008/01x` are errors (fail `validate`); `003/004/005/006/013/02x` are warnings (pass `validate` unless `--strict`). Don't describe a warning code as an error — `doctor.md`'s sample output and the skill depend on getting this right.
- **The no-live-HTTP boundary is duplicated, not linked.** It's in both `SKILL.md` and `agents/httpchain-author.md` because the subagent can't see the skill's context. Tighten one → tighten both.
- **The skill `description` is load-bearing.** Keep the concrete trigger phrases (`test_<name>.http.json`, "scenario", "`$ref`/`$include`", "`HTTPCHAINxxx`", "chain HTTP calls"). Dropping them narrows triggering; adding unrelated phrases broadens it past the plugin's competence.
- **Run `validate` through the project runner** (`uv run pytest-httpchain …`, or `pytest-httpchain …` in an active venv). `--deep` imports the user's `module:func` code, so it must run in the project env — never `uvx`, which has no project imports.
- **Version bumps touch two files:** `.claude-plugin/plugin.json` and the matching `.claude-plugin/marketplace.json` entry (`name`/`description`/`version` in sync; `license` lives only in `marketplace.json`). CI enforces the version sync via the repo-root `scripts/check-version-sync.sh`.
