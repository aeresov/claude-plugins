---
name: pytest-httpchain
description: Author, edit, and validate pytest-httpchain HTTP API test scenarios — declarative JSON files (test_<name>.http.json) with multi-stage request chaining, {{ template }} expressions, $ref/$include composition, JMESPath saves, and verify steps. Activate when the user writes, edits, debugs, or validates an httpchain scenario, asks to chain HTTP calls in a test, fix an HTTPCHAINxxx diagnostic, or set up pytest-httpchain. Validates after authoring; dispatches the httpchain-author subagent for multi-stage work.
---

# pytest-httpchain test authoring

pytest-httpchain is a pytest plugin for declarative HTTP API integration testing. Test scenarios are JSON files discovered by pattern `test_<name>.http.json` (the `http` suffix is the default of the `httpchain_suffix` ini option — named `suffix` before 0.10, and that old name is ignored since 0.11).

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
- **A multi-stage chain, multi-file `$ref`/`$include` composition, parametrized/parallel stages, or anything needing several validate→fix rounds → dispatch the [`httpchain-author`](../../agents/httpchain-author.md) subagent.** Hand it the goal, the target `test_*.http.json` path, the API shape (endpoints, auth, expected codes), and any `--syspath` directory where `module:func` references live. It authors and statically validates in an isolated context and returns the finished scenario. It **never makes live HTTP calls** — it only writes JSON and runs the read-only `pytest-httpchain` subcommands (`validate`, `resolve`, `show`, `graph`, `schema`).

Always `validate` after authoring, inline or not (see *Validate your scenario* below).

---

## Scenario structure

```json
{
  "description": "optional scenario description",
  "marks": ["optional_pytest_markers"],
  "fixtures": ["fixture_name"],
  "auth": "mymodule:get_auth",
  "ssl": { "verify": true, "cert": null },
  "substitutions": [],
  "stages": []
}
```

- `fixtures` — pytest fixtures available to every stage (a stage's own `fixtures` add to them).
- `auth` — default auth for every request: a `"module:func"` returning an `httpx.Auth`, or `{"name": "module:func", "kwargs": {...}}`. A request's own `auth` overrides it.
- `ssl` — `verify`: `true` / `false` / a CA-bundle path; `cert`: a client-cert path or `[cert_path, key_path]`.
- Relative file paths (`ssl`, `body.binary`/`files`, `verify.body.schema`) resolve against the scenario file's directory.
- Scenario-level `substitutions`/`auth`/`ssl` resolve once, before any fixture exists — referencing a fixture there is an error (`HTTPCHAIN016`).

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
  "allow_redirects": true,
  "auth": { "name": "mymodule:special_auth", "kwargs": { "role": "admin" } }
}
```

`auth` is optional (same forms as scenario-level `auth`, which it overrides for this request).

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
      "headers": { "content-type": { "contains": "application/json" } },
      "user_functions": ["mymodule:check_response"],
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

**Verify fields:**
- `headers` — a plain string is an **exact, full-string** match (`"application/json"` fails against `application/json; charset=utf-8`). For partial/pattern checks use a matcher object with any of `contains`, `not_contains`, `matches`, `not_matches` (regex, `re.search`); an absent header counts as `""`.
- `body.schema` — an inline JSON Schema (standard JSON Schema, verbatim: `$include`/`$merge` are **not** resolved inside it — `HTTPCHAIN028`) or a schema **file path** (`"./schemas/user.json"`, relative to the scenario file).
- `user_functions` — `"module:func"` or `{"name": ..., "kwargs": {...}}`; each is called with the `httpx.Response` (plus kwargs) and returns a bool.
- `expressions` — `{{ }}` templates that must each evaluate to a bool.

**Important — what templates can see in response steps:** `verify.expressions` and save templates are evaluated against the **context** (saved variables, fixtures, substitutions) plus a reserved **`response` metadata namespace**: `response.status`, `response.reason`, `response.headers` (case-insensitive), `response.elapsed_ms` — e.g. `"{{ response.status == 200 }}"`, or save a header with `{"save": {"substitutions": [{"vars": {"etag": "{{ response.headers['etag'] }}"}}]}}`. The namespace exists only in response steps (not in request templates), and a user variable/save/fixture named `response` is shadowed there (`HTTPCHAIN027`). The response **body** is not in it: check it with `verify.body`, or `save` the value first (e.g. via `jmespath`) and reference the saved variable in a later `expressions` step (as shown above).

**Save types:**
- `{"jmespath": {...}}` - extract values from JSON response via JMESPath
- `{"substitutions": [...]}` - compute values using template expressions
- `{"user_functions": [...]}` - call Python functions (given the `httpx.Response`) that each return a dict of names to save; `validate` can't see those names, so later uses draw an expected `HTTPCHAIN003`

## Template expressions

Use `{{ expr }}` syntax. Expressions are evaluated with Python semantics.

**Available context:** all saved variables, fixture values, and substitution results.

**Built-in functions:** `len`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `range`, `zip`, `enumerate`, `bool`, `int`, `float`, `str`, `dict`, `list`, `tuple`, `set`, `uuid4()`, `env('VAR', default)`, `get('name', default)`, `exists('name')`, `rand()`, `randint(top)` (one argument: an int `0 <= n < top`)

`env`, `get` and `exists` take the **name as a quoted string** — `exists(token)` passes the variable's *value* (wrong answer, or an undefined-variable error).

**JSON literals:** `true`, `false`, `null` map to Python `True`, `False`, `None`.

**Access rules:** `vars` substitution objects become namespaces — use attribute access (`{{ user.id }}`; `{{ user['id'] }}` fails at runtime, which `validate` can't catch). JMESPath-saved values, fixture dicts and `combinations` dicts are plain dicts — use subscripts (`{{ item['id'] }}`). A `functions` substitution binds a callable: call it (`{{ generate_token() }}`).

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

It checks structure plus semantics a JSON Schema cannot, each with a stable `HTTPCHAINxxx` code and a severity. **Errors** fail `validate`; **warnings** pass it (`OK with warnings`) unless `--strict`; `HTTPCHAIN025` is **info** and never affects the result. Common ones:

- `HTTPCHAIN003` (warning) — a `{{ var }}` that is never defined, saved, or provided as a fixture (likely a typo).
- `HTTPCHAIN004` (warning) — a variable used **before** it is saved or defined: saved by a later stage or a later step of the same response, or used in a stage's request when it is only saved in that same stage's response. Remember: a value `save`d in a stage's response is available to *later* response steps and *later* stages, never to the request that produced it.
- `HTTPCHAIN006` (warning) — a `verify` step that asserts nothing.
- `HTTPCHAIN007` / `HTTPCHAIN008` (errors) — body or header-matcher `contains`/`not_contains` (or `matches`/`not_matches`) that list the same value, which can never pass.
- `HTTPCHAIN012` (error) — `$include`/`$merge`/`$ref` failed to resolve, including a sibling `Merge conflict` (see `references/composition.md`).

A warning is still a finding, not a pass — most flag something that won't behave as written at runtime. Add `--format json` for machine-readable output. The same checks run automatically during `pytest --collect-only`.

For a deeper check that imports your `module:func` references (confirming they resolve and their signatures match — including the injected `response` for save/verify functions) and verifies referenced files/schemas exist, add `--deep` (optionally `--syspath <dir>` for import roots, `--strict` to fail on warnings). Deep findings (`HTTPCHAIN020`–`024`) are always **warnings**, so an unimportable function still exits 0 without `--strict` — read the output. `--deep` imports your code, so it **must** run in the project environment, not via `uvx`:

```bash
uv run pytest-httpchain validate --deep --syspath . test_<name>.http.json
```

Fix every reported issue and re-validate until clean. Then hand the user the run command (`uv run pytest -k <name>`) — do **not** run the suite yourself; firing the live HTTP calls is the user's call, not this skill's. For a whole-suite health check, use `/pytest-httpchain:doctor`.

Note: in response-step templates only the `response.status`/`reason`/`headers`/`elapsed_ms` metadata is ambient — the response **body** is not; `save` what you need from it first, then reference the saved variable.

## Inspect (read-only, pytest-httpchain ≥0.5.0)

Four CLI commands explain a scenario without running it — all static, no network:

- `pytest-httpchain resolve <file>` — print the scenario with every `$ref`/`$include`/`$merge` inlined. See what a composed, multi-file scenario actually becomes.
- `pytest-httpchain show <file>` — summarize stages and variable data-flow: per stage, what it `saves` and `consumes`, and which earlier stage produced each consumed value. Add `--format json` for machine output.
- `pytest-httpchain graph <file>` — emit a Mermaid flowchart of the stage data-flow (`--direction LR` for wide chains). Render it to show the user how a scenario chains.
- `pytest-httpchain schema > scenario.schema.json` — emit the scenario JSON Schema (stdout only; `-o` was removed in 0.8.0) for editor autocomplete/validation.

`show` and `graph` need the scenario to load — run `validate` first if they error; `resolve` works even when a scenario doesn't fully validate.
