---
name: pytest-httpchain
description: Author, edit, and validate pytest-httpchain HTTP API test scenarios — declarative JSON files (test_<name>.http.json) with multi-stage request chaining, {{ template }} expressions, $ref/$include composition, JMESPath saves, and verify steps. Activate when the user writes, edits, debugs, or validates an httpchain scenario, asks to chain HTTP calls in a test, fix an HTTPCHAINxxx diagnostic, or set up pytest-httpchain. Validates after authoring; dispatches the httpchain-author subagent for multi-stage work.
---

# pytest-httpchain test authoring

pytest-httpchain is a pytest plugin for declarative HTTP API integration testing. Test scenarios are JSON files discovered by pattern `test_<name>.http.json` (the `http` suffix is the `suffix` ini option's default).

This skill owns scenario *authoring* and the *validate → fix* loop; the `pytest-httpchain` package (a dev dependency of the project under test) supplies the `validate` CLI and the pytest collection that run scenarios.

## Setup check (do this first)

Before authoring, confirm the package is importable in the project's **test** environment — the same interpreter `pytest` runs under:

```bash
uv run python -c "import pytest_httpchain" 2>/dev/null && echo ok   # uv project
python   -c "import pytest_httpchain" 2>/dev/null && echo ok        # active venv
```

Missing? Stop and run `/pytest-httpchain:setup` (or have the user add it as a dev dependency) — without it you can't `validate`.

## Inline or dispatch the subagent?

- **A single small scenario, or a one-line edit → do it inline** with the reference below, then validate.
- **A multi-stage chain, multi-file `$ref`/`$include` composition, parametrized/parallel stages, or anything needing several validate→fix rounds → dispatch the [`httpchain-author`](../../agents/httpchain-author.md) subagent.** Hand it the goal, the target `test_*.http.json` path, the API shape (endpoints, auth, expected codes), and any `--syspath` directory where `module:func` references live. It authors and statically validates in an isolated context and returns the finished scenario. It **never makes live HTTP calls** — it only writes JSON and runs `pytest-httpchain validate`.

Always `validate` after authoring, inline or not (see *Validate your scenario* below).

---

## Scenario structure

```json
{
  "description": "optional scenario description",
  "marks": ["optional_pytest_markers"],
  "substitutions": [],
  "stages": []
}
```

## Stage structure

```json
{
  "name": "stage name",
  "description": "optional",
  "fixtures": ["fixture_name"],
  "marks": ["skip", "xfail(reason='not ready')"],
  "always_run": false,
  "substitutions": [],
  "parametrize": [],
  "parallel": null,
  "request": { ... },
  "response": [ ... ]
}
```

Stages run sequentially and share a global context. Values saved in one stage are available in subsequent stages.

Stages can also be written as a dict (keys become stage names):

```json
{
  "stages": {
    "create user": { "request": { ... }, "response": [ ... ] },
    "get user":    { "request": { ... }, "response": [ ... ] }
  }
}
```

## Request

```json
{
  "url": "{{ server }}/api/users",
  "method": "POST",
  "headers": { "Authorization": "Bearer {{ token }}" },
  "params": { "page": 1 },
  "body": { "json": { "name": "Alice" } },
  "timeout": 30.0,
  "allow_redirects": true
}
```

**Body types** (use exactly one key):
- `{"json": { ... }}` - JSON body
- `{"form": { ... }}` - URL-encoded form
- `{"text": "..."}` - raw text
- `{"xml": "<root/>"}` - XML
- `{"base64": "..."}` - base64-encoded binary
- `{"binary": "/path/to/file"}` - file upload
- `{"files": {"field": "/path/to/file"}}` - multipart file upload
- `{"graphql": {"query": "...", "variables": {}}}` - GraphQL

## Response steps

Response is a list of verify and save steps, executed in order:

```json
"response": [
  {
    "verify": {
      "status": 200,
      "headers": { "content-type": "application/json" },
      "body": {
        "schema": { "type": "object", "required": ["id"] },
        "contains": ["expected text"],
        "not_contains": ["error"],
        "matches": ["\\d{4}-\\d{2}-\\d{2}"],
        "not_matches": ["forbidden"]
      }
    }
  },
  {
    "save": {
      "jmespath": {
        "user_id": "data.id",
        "user_name": "data.name",
        "total": "length(items)"
      }
    }
  },
  {
    "verify": {
      "expressions": [
        "{{ total > 0 }}",
        "{{ user_name != '' }}"
      ]
    }
  }
]
```

**Important:** `verify.expressions` are `{{ }}` templates evaluated against the **context** (saved variables, fixtures, substitutions). The HTTP response is **not** ambient in templates — there is no `response`/`status_code`/`body`/`json` variable. To assert on response data, either:
- use `verify.status`, `verify.headers`, `verify.body` (these check the response directly), or
- `save` the value first (e.g. via `jmespath`) and reference the saved variable in a later `expressions` step (as shown above).

**Save types:**
- `{"jmespath": {...}}` - extract values from JSON response via JMESPath
- `{"substitutions": [...]}` - compute values using template expressions
- `{"user_functions": [...]}` - call Python functions to process response

## Template expressions

Use `{{ expr }}` syntax. Expressions are evaluated with Python semantics.

**Available context:** all saved variables, fixture values, and substitution results.

**Built-in functions:** `len`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `range`, `zip`, `enumerate`, `bool`, `int`, `float`, `str`, `dict`, `list`, `tuple`, `set`, `uuid4()`, `env(var, default)`, `get(var, default)`, `exists(var)`, `rand()`, `randint(a, b)`

**JSON literals:** `true`, `false`, `null` map to Python `True`, `False`, `None`.

## References

Load these as the task needs them:

- [`references/composition.md`](references/composition.md) — substitutions, `$include` / `$merge` / `$ref`, parametrize, parallel execution.
- [`references/example.md`](references/example.md) — a complete multi-stage scenario, end to end.

## Validate your scenario

**Always validate after authoring or editing** — no server or network needed. Run it through the project's environment so user-function imports resolve against the project's code:

```bash
uv run pytest-httpchain validate test_<name>.http.json     # uv project
pytest-httpchain validate test_<name>.http.json            # active venv
uvx pytest-httpchain validate test_<name>.http.json        # ad-hoc, shallow only (no project imports)
```

It checks structure plus semantics a JSON Schema cannot, each with a stable `HTTPCHAINxxx` code:

- `HTTPCHAIN003` — a `{{ var }}` that is never defined, saved, or provided as a fixture (likely a typo).
- `HTTPCHAIN004` — a variable used **before** the stage that saves it, or used in a stage's request when it is only saved in that same stage's response. Remember: a value `save`d in a stage's response is available to *later* response steps and *later* stages, never to the request that produced it.
- `HTTPCHAIN006` — a `verify` step that asserts nothing.
- `HTTPCHAIN007` / `HTTPCHAIN008` — body `contains`/`not_contains` (or `matches`/`not_matches`) that list the same value, which can never pass.

Add `--format json` for machine-readable output. The same checks run automatically during `pytest --collect-only`.

For a deeper check that imports your `module:func` references (confirming they resolve and their signatures match — including the injected `response` for save/verify functions) and verifies referenced files/schemas exist, add `--deep` (optionally `--syspath <dir>` for import roots, `--strict` to fail on warnings). `--deep` imports your code, so it **must** run in the project environment, not via `uvx`:

```bash
uv run pytest-httpchain validate --deep --syspath . test_<name>.http.json
```

Fix every reported issue and re-validate until clean. Then hand the user the run command (`uv run pytest -k <name>`) — do **not** run the suite yourself; firing the live HTTP calls is the user's call, not this skill's. For a whole-suite health check, use `/pytest-httpchain:doctor`.

Note: the HTTP response is **not** ambient in `{{ }}` templates — `save` what you need from a response first, then reference the saved variable.

## Inspect (read-only, pytest-httpchain ≥0.5.0)

Four CLI commands explain a scenario without running it — all static, no network:

- `pytest-httpchain resolve <file>` — print the scenario with every `$ref`/`$include`/`$merge` inlined. See what a composed, multi-file scenario actually becomes.
- `pytest-httpchain show <file>` — summarize stages and variable data-flow: per stage, what it `saves` and `consumes`, and which earlier stage produced each consumed value. Add `--format json` for machine output.
- `pytest-httpchain graph <file>` — emit a Mermaid flowchart of the stage data-flow (`--direction LR` for wide chains). Render it to show the user how a scenario chains.
- `pytest-httpchain schema -o <path>` — write the scenario JSON Schema for editor autocomplete/validation.

`show` and `graph` need the scenario to load — run `validate` first if they error; `resolve` works even when a scenario doesn't fully validate.
