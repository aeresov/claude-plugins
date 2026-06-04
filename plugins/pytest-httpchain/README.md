# pytest-httpchain

A Claude Code plugin for authoring and validating [pytest-httpchain](https://github.com/aeresov/pytest-httpchain) scenarios — the declarative JSON format for HTTP API integration tests (`test_<name>.http.json`: multi-stage request chaining, `{{ template }}` expressions, `$ref`/`$include` composition, JMESPath saves, verify steps).

It's a **companion** to the package, not a copy of it: the package supplies the `validate` CLI and the pytest collection that run scenarios; this plugin supplies the authoring skill, a static-validating subagent, and `/setup` + `/doctor`.

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install pytest-httpchain@aeresov-claude-plugins
/pytest-httpchain:setup     # confirm the dev dependency, optionally scaffold an example
```

Then ask Claude to write or fix an HTTP API test ("a scenario that logs in, creates an order, deletes it") — the skill authors the JSON and validates it. Run `/pytest-httpchain:doctor` for a read-only health check.

## Prerequisites

A Python project whose test environment has `pytest-httpchain` as a dev dependency. `/pytest-httpchain:setup` detects your package manager (uv / poetry / pdm / pip) and prints the add command; it never installs for you. No servers, credentials, or network are needed to author and validate — running the scenarios (the live HTTP calls) is `pytest`'s job, which you drive.

## What triggers the skill

Authoring or editing a scenario, composing one from `$ref`/`$include` fragments, debugging or validating one, fixing an `HTTPCHAINxxx` diagnostic, or setting up pytest-httpchain. It does **not** fire on general HTTP client code (requests/httpx scripts) or non-pytest API tests.

## What's in scope vs out

| Scope                                                       | In / Out |
|-------------------------------------------------------------|----------|
| Authoring / editing `test_*.http.json` scenarios            | **In**   |
| `$ref`/`$include`, templates, parametrize, parallel stages  | **In**   |
| Static validation + fixing `HTTPCHAINxxx` diagnostics       | **In**   |
| Project setup + a read-only health check                    | **In**   |
| **Running** scenarios / making live HTTP calls              | **Out** (you run `pytest`) |
| Writing the API under test                                  | **Out**  |
| requests/httpx/aiohttp driver code, non-httpchain tests     | **Out**  |

The skill and subagent author and **statically validate** only — they never fire a request at a real endpoint, because a scenario routinely contains `POST`/`PUT`/`DELETE` and they can't know the target is safe. A clean scenario comes back with the `pytest` command to run it yourself.

## The `httpchain-author` subagent

For a non-trivial scenario — a multi-stage chain, multi-file `$ref`/`$include` composition, parametrized or parallel stages, or one needing several validate→fix rounds — the skill dispatches a context-isolated subagent. It loops `write → pytest-httpchain validate --deep → fix` until clean, then returns the scenario and the command to run it. It runs only `pytest-httpchain validate` — never a live HTTP call.

See [`agents/httpchain-author.md`](agents/httpchain-author.md).

## Commands

- **`/pytest-httpchain:setup`** — confirms the dev dependency (guiding the install per your package manager, never running it), optionally sets the pytest `suffix` ini, optionally scaffolds an example scenario + `conftest.py`. Writes only files you approve.
- **`/pytest-httpchain:doctor`** — read-only: package manager · package installed (+ version) · scenarios discoverable · every scenario passes `validate`. One line per check, with a fix for each failure.

## Validation & inspection

`validate` catches what a JSON Schema can't — undefined variables, use-before-save data-flow, no-op verifies, contradictory body checks — each with a stable `HTTPCHAINxxx` code. `--deep` also imports `module:func` references and checks their signatures. The same checks run at `pytest --collect-only`.

Four read-only inspection commands (pytest-httpchain ≥0.5.0) round it out: `resolve` (inline `$ref`/`$include`/`$merge`), `show` (stage + data-flow summary), `graph` (Mermaid flowchart), and `schema` (emit the JSON Schema for editors). The skill and subagent use them to check composition and data-flow without running the suite.

## License

AGPL-3.0-only — the whole `claude-plugins` repo is AGPL for consistency. The plugin only invokes the (MIT-licensed) `pytest-httpchain` package; no package code is bundled. See [LICENSE](../../LICENSE).
