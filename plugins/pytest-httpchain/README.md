# pytest-httpchain

A Claude Code and Codex plugin for authoring and validating [pytest-httpchain](https://github.com/aeresov/pytest-httpchain) scenarios — the declarative JSON format for HTTP API integration tests (`test_<name>.http.json` / `.jsonc`: multi-stage request chaining, `{{ template }}` expressions, `$ref`/`$include` composition, JMESPath saves and assertions, retry/polling, parallel load stages, connection stages for non-HTTP protocols).

It's a **companion** to the package, not a copy of it: the package supplies the `validate` CLI and the pytest collection that run scenarios; this plugin supplies the authoring skill, a static-validating subagent, and `/setup` + `/doctor`.

The authoring reference tracks **pytest-httpchain 0.19.0**. It works with older installs too: the skill reads the installed version and keeps to what that version supports (its *Version gates* table).

## Quick start

```bash
/plugin marketplace add aeresov/claude-plugins
/plugin install pytest-httpchain@aeresov-claude-plugins
/pytest-httpchain:setup     # confirm the dev dependency, optionally scaffold an example
```

Then ask the assistant to write or fix an HTTP API test ("a scenario that logs in, creates an order, polls until it ships, deletes it"), or hand it a HAR recording or a few curl commands to start from — the skill authors the JSON and validates it. Run `/pytest-httpchain:doctor` for a read-only health check.

## Codex

Register this repository and install `pytest-httpchain`:

```bash
codex plugin marketplace add aeresov/claude-plugins
codex plugin add pytest-httpchain@aeresov-codex-plugins
```

Use `$pytest-httpchain:pytest-httpchain-setup` and `$pytest-httpchain:pytest-httpchain-doctor` in a Codex prompt. The main skill is `$pytest-httpchain:pytest-httpchain`. Both clients use the same skills, implementation and (where applicable) gitignored `.claude/` project settings. These workflows need local execution with the prerequisites below. No global Codex configuration is changed by cloning this repository.

Claude registers the named subagent described below. Codex reads the same procedure, delegates through its available subagents when possible, and otherwise follows it inline. Claude-specific model/tool declarations do not carry over to Codex.

## Prerequisites

A Python project whose test environment has `pytest-httpchain` as a dev dependency. `/pytest-httpchain:setup` detects your package manager (uv / poetry / pdm / pip) and prints the add command; it never installs for you. No servers, credentials, or network are needed to author and validate — running the scenarios (the live HTTP calls) is `pytest`'s job, which you drive.

Setup, doctor and authoring use the existing test interpreter directly. Create/sync that environment yourself first; these workflows report a missing environment and leave dependency files and lockfiles alone. Doctor uses package-owned directory validation on pytest-httpchain ≥0.16, including both `.json` and `.jsonc` scenarios. Older versions retain JSON-file validation and report JSONC as unsupported.

## What triggers the skill

Authoring or editing a scenario, composing one from `$ref`/`$include` fragments, turning a curl command or HAR recording into one, debugging or validating one, fixing an `HTTPCHAINxxx` diagnostic, or setting up pytest-httpchain. It does **not** fire on general HTTP client code (requests/httpx scripts) or non-pytest API tests.

## What's in scope vs out

| Scope                                                       | In / Out |
|-------------------------------------------------------------|----------|
| Authoring / editing `test_*.http.json` / `.jsonc` scenarios | **In**   |
| `$ref`/`$include`, templates, parametrize, parallel stages  | **In**   |
| `client` config, built-in auth, `verify.jmespath`, retry/polling, `skip_if` | **In** |
| Connection stages (the scenario side of a non-HTTP protocol) | **In**  |
| A starter scenario from a HAR file or curl commands (`import`) | **In** |
| Static validation + fixing `HTTPCHAINxxx` diagnostics       | **In**   |
| Project setup + a read-only health check                    | **In**   |
| **Running** scenarios / making live HTTP calls              | **Out** (you run `pytest`) |
| Writing the API under test                                  | **Out**  |
| User functions and connection adapters (your project's Python) | **Out** unless you ask for one |
| requests/httpx/aiohttp driver code, non-httpchain tests     | **Out**  |

The skill and subagent author and **statically validate** only — they never fire a request at a real endpoint, because a scenario routinely contains `POST`/`PUT`/`DELETE` and they can't know the target is safe. A curl command you paste is input for `import`, read from a file — it is never executed. A clean scenario comes back with the `pytest` command to run it yourself.

## The `httpchain-author` subagent

For a non-trivial scenario — a multi-stage chain, multi-file `$ref`/`$include` composition, parametrized or parallel stages, retry/polling or connection stages, or one needing several validate→fix rounds — the skill dispatches a context-isolated subagent. It loops `write → (resolve / show, for composed or multi-stage scenarios) → validate → fix` until clean — adding `--deep` only when the scenario references `module:func` code, connection adapters or files — then returns the scenario and the command to run it. It runs only the static `pytest-httpchain` subcommands (`validate`, `resolve`, `show`, `graph`, `schema`, `import`) — never a live HTTP call.

See the shared [authoring procedure](skills/pytest-httpchain/references/authoring.md) and the [Claude agent wrapper](agents/httpchain-author.md).

## Commands

- **`/pytest-httpchain:setup`** — confirms the dev dependency (guiding the install per your package manager, never running it), optionally sets the `httpchain_suffix` pytest ini option (pytest-httpchain ≥0.10) in whichever pytest config table the project already uses, optionally scaffolds an example scenario + `conftest.py`. Writes only files you approve.
- **`/pytest-httpchain:doctor`** — read-only: package manager · package installed (+ version) · scenarios discoverable · every scenario passes `validate`. One line per check, with a fix for each failure.

## Validation & inspection

`validate` catches what a JSON Schema can't — undefined variables, use-before-save data-flow, no-op verifies, contradictory body checks, a relative URL without a base, a template the engine would refuse — each with a stable `HTTPCHAINxxx` code. `--deep` also imports `module:func` references and connection adapters and checks their signatures. The same checks run at `pytest --collect-only`. The skill carries the full code table with severities and fixes ([`references/diagnostics.md`](skills/pytest-httpchain/references/diagnostics.md)).

Four read-only inspection commands (pytest-httpchain ≥0.5.0) round it out: `resolve` (inline `$ref`/`$include`/`$merge`), `show` (stage + data-flow summary), `graph` (Mermaid flowchart), and `schema` (emit the JSON Schema for editors). The skill and subagent use them to check composition and data-flow without running the suite. `import har` / `import curl` (≥0.16) write a starter scenario from recorded traffic, with secrets replaced by environment-variable placeholders.

## License

AGPL-3.0-only — the whole `claude-plugins` repo is AGPL for consistency. The plugin only invokes the (MIT-licensed) `pytest-httpchain` package; no package code is bundled. See [LICENSE](../../LICENSE).
