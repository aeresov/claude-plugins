---
name: pytest-httpchain
description: Author, edit, and validate pytest-httpchain HTTP API test scenarios — declarative JSON files (test_*.http.json or .jsonc) with multi-stage request chaining, {{ template }} expressions, $ref/$include composition, JMESPath saves and verify steps, plus retry/polling, parallel load stages and connection stages for non-HTTP protocols. Activate when the user writes, edits, debugs, or validates an httpchain scenario, asks to chain HTTP calls in a test, turn a curl command or HAR recording into a scenario, fix an HTTPCHAINxxx diagnostic, or set up pytest-httpchain. Validates after authoring and follows a shared authoring procedure for multi-stage work.
---

# pytest-httpchain test authoring


## Client compatibility

This skill supports Claude Code and Codex. Resolve `../..` from this SKILL.md's directory to the absolute plugin root and substitute it for `<plugin-root>` below. Keep shell calls in the consuming project directory.

pytest-httpchain is a pytest plugin for declarative HTTP API integration testing. Test scenarios are JSON files discovered by pattern `test_<name>.http.json`, plus `test_<name>.http.jsonc` on package ≥0.16 (the `http` suffix is the default of the `httpchain_suffix` ini option — named `suffix` before 0.10, and that old name is ignored since 0.11). Preserve an existing supported `.jsonc` filename when editing.

This skill owns scenario *authoring* and the *validate → fix* loop; the `pytest-httpchain` package (a dev dependency of the project under test) supplies the `validate` CLI and the pytest collection that run scenarios.

This skill documents pytest-httpchain **0.19.0**. The installed package decides what a scenario may use: on an older one, stay inside [Version gates](#version-gates); on a newer one, or whenever a field name is in doubt, the `schema` subcommand prints the exact JSON Schema of the installed version, and `validate` has the last word.

## Setup check (do this first)

Before authoring, read checks 1–2 of `<plugin-root>/setup-checklist.md` and select the project's **existing test interpreter**, the same one pytest uses. Run its version probe and retain the installed version and literal absolute interpreter path as `<test-python>`. Use `"<test-python>" -B -m pytest_httpchain.cli` for every inspection and validation command, including delegated work. This works in uv, Poetry, PDM and active-venv projects without switching to a global installation. Do not create/sync an environment or update a lockfile; even `uv run --no-sync` can create a missing venv.

Missing environment or package? Stop and run `pytest-httpchain-setup` (or have the user create/sync the test environment or add the dev dependency) — without it you can't `validate`.

## Inline or dispatch the subagent?

- **A single small scenario, or a one-line edit → do it inline** with the reference below, then validate.
- **A multi-stage chain, multi-file `$ref`/`$include` composition, parametrized/parallel stages, connection stages, or several validate→fix rounds → follow [the authoring procedure](references/authoring.md).** In Claude Code, dispatch the registered `httpchain-author` agent. In Codex, use an available subagent with the absolute procedure path, this skill's path, the goal, target scenario path, API shape, selected interpreter/version and any `--syspath` directories; instruct it to read both files before authoring. If delegation is unavailable, follow the procedure inline. Do not assume Claude's agent registration or `sonnet` model exists in Codex. The author **never makes live HTTP calls** — it only writes JSON and runs the static subcommands `validate`, `resolve`, `show`, `graph`, `schema`, `import`.

Always `validate` after authoring, inline or not (see *Validate your scenario* below).

## Version gates

Compare the installed version from the setup check before using a feature. Below its gate the key is unknown, and the scenario fails validation (`HTTPCHAIN000`) or the template its stage.

| Needs | Feature |
|-------|---------|
| ≥0.5 | `resolve` / `show` / `graph` / `schema` subcommands |
| ≥0.10 | the `httpchain_suffix` ini option (`suffix` before it) |
| ≥0.16 | `.jsonc` files, comments and trailing commas · `validate <directory>` and `--suffix` · the `client` block and relative URLs · built-in `auth` (`basic`, `digest`, `bearer`, `false`) · `verify.status` classes and lists · `verify.jmespath` · `save.regex` · a `multipart` body and file objects · `skip_if` · `retry` · `parallel.collect_saves` / `thresholds` / `stats_as` · a JSON pointer on a schema file · the time, encoding, URL and hashing template functions · the `\{{` escape · reading a `vars` object by key · `import har` / `import curl` |
| ≥0.18 | `msgpack` and `bytes` bodies · `response_codec` · `msgpack_pack()`, `hex_bytes()`, `hexencode()`, `b64decode_bytes()` |
| ≥0.19 | `connections` and `connection` stages · `sha1()` |

On a package below 0.16, assert on the body by saving a value and testing it in `expressions`, write URLs absolute, and authenticate with a header or a user function.

---

## Scenario structure

```json
{
  "description": "optional scenario description",
  "marks": ["optional_pytest_markers"],
  "fixtures": ["fixture_name"],
  "auth": { "bearer": "{{ api_token }}" },
  "ssl": { "verify": true, "cert": null },
  "client": { "base_url": "https://api.example.com/v1", "headers": { "Accept": "application/json" } },
  "connections": {},
  "substitutions": [],
  "stages": []
}
```

- Keys are checked strictly at every level: an unknown or misspelled one fails validation, naming it. Only `$schema` and the `$ref`/`$include`/`$merge` directives are extra.
- `fixtures` — pytest fixtures available to every stage (a stage's own `fixtures` add to them). `marks` apply to every stage; an `xdist_group` mark belongs here, never on a stage (`HTTPCHAIN031`).
- `auth` — default auth for every request (forms under [Authentication](#authentication)). A request's own `auth` overrides it.
- `ssl` — `verify`: `true` / `false` / a CA-bundle path; `cert`: a client-cert path or `[cert_path, key_path]`.
- `client` — the one HTTP client all stages share: `base_url` (makes stage URLs relative), `headers` and `params` sent with every request, `timeout` (default 30), `follow_redirects` (`true`), `max_redirects` (20), `proxy`, `http2` (`true`), `max_connections` (`null`, no limit), `max_keepalive_connections` (20). Cookies a response sets are sent by every later stage of the scenario.
- `connections` — named non-HTTP connections for `connection` stages: see [`references/connections.md`](references/connections.md).
- Relative file paths (`ssl`, `body.binary`, uploaded files, `verify.body.schema`) resolve against the scenario file's directory.
- Scenario-level `substitutions`/`auth`/`ssl`/`client` resolve once, before any fixture or save exists: a fixture there is an error (`HTTPCHAIN016`), and so is any other name the scenario `substitutions` do not define (`HTTPCHAIN017`). Read the environment with `env('VAR')`.
- Every file is read as JSON with comments: `//`, `/* */` and one trailing comma are accepted in `.json` and `.jsonc` alike. `NaN` and `Infinity` are syntax errors (`HTTPCHAIN014`); write `"{{ float('inf') }}"`.

## Stage structure

```json
{
  "name": "stage name",
  "description": "optional",
  "fixtures": ["fixture_name"],
  "marks": ["skip", "xfail(reason='not ready')"],
  "always_run": false,
  "skip_if": false,
  "substitutions": [],
  "parametrize": [],
  "parallel": null,
  "retry": null,
  "request": { ... },
  "response": [ ... ]
}
```

A stage is one HTTP `request` **or** one `connection` step, never both. Stages run in order and share one context: values saved in a stage are available to later stages. A stage that fails discards its saves and aborts the chain — the stages after it are skipped unless `always_run` lets them through.

- `name` — may not contain `::` (`HTTPCHAIN032`), and must be unique (`HTTPCHAIN001`).
- `always_run` — `true`, or a template judged by truthiness when the stage is about to be skipped after a failure: `"{{ exists('order_id') }}"` for a cleanup that only makes sense once something was created.
- `skip_if` — `true`, or a template that must evaluate to a real boolean, decided when the stage is about to run: `"{{ target == 'prod' }}"`. A skipped stage saves nothing and the chain goes on.
- `retry` — attempt the stage again while it fails, for polling: `{"attempts": 10, "delay": 0.5, "backoff": 2, "max_delay": 5, "on": "verify"}`.
- `response_codec` — `"json"` or `"msgpack"` when a server mislabels its body; the default `"auto"` decodes MessagePack by `Content-Type`.

`always_run`, `skip_if`, `retry`, and what they do to the names later stages read, are in [`references/flow-control.md`](references/flow-control.md).

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
  "url": "/users/{{ user_id }}",
  "method": "POST",
  "headers": { "X-Request-Id": "{{ uuid4() }}" },
  "params": { "page": 1, "tag": ["a", "b"] },
  "body": { "json": { "name": "Alice" } },
  "auth": { "bearer": "{{ token }}" },
  "timeout": 120.0,
  "allow_redirects": false
}
```

- `url` — an absolute `http`/`https` URL, or one relative to `client.base_url`, sent as written. A relative URL is appended to the base URL's path: base `https://api.example.com/v1` and `/users/1` give `https://api.example.com/v1/users/1` (a leading `/` does not return to the host's root). Relative without a `base_url` is an error (`HTTPCHAIN034`).
- `method` — default `GET`.
- `params` — merged into a query the URL already has: a key in both takes its value from `params`, and a list repeats its key. `client.params` only fill in the keys a request does not set.
- `headers` — replace `client.headers` of the same name, case-insensitively. A header value is text: write `"{{ str(timestamp()) }}"`, not a bare number.
- `timeout`, `allow_redirects` — leave them out to take `client.timeout` and `client.follow_redirects`; a stage's own value wins even where it equals the default.

**Body types** (use exactly one key):
- `{"json": { ... }}` - JSON body (`null` sends the JSON document `null`)
- `{"form": { ... }}` - URL-encoded form
- `{"text": "..."}` - raw text
- `{"xml": "<root/>"}` - XML
- `{"base64": "..."}` - base64-encoded binary
- `{"binary": "./file.bin"}` - a file's bytes as the body
- `{"files": {"field": "./file.pdf"}}` - multipart upload of files only
- `{"multipart": {"fields": {"title": "Q3", "tags": ["a", "b"]}, "files": {"document": "./report.pdf"}}}` - multipart form fields and files together
- `{"graphql": {"query": "...", "variables": {}}}` - GraphQL
- `{"msgpack": { ... }}` - a value packed as MessagePack; `{"bytes": "{{ msgpack_pack(packet) }}"}` - raw bytes from one template

A file in `files` or `multipart.files` is a path, a list of files, or an object with exactly one of `path`, `content` (text) and `base64`, plus optional `filename` and `content_type`: `{"content": "{\"a\": 1}", "filename": "meta.json", "content_type": "application/json"}`.

### Authentication

At scenario level (every request) or in one request (replacing the scenario's):

| `auth` | Sends |
|--------|-------|
| `{"basic": {"username": "...", "password": "..."}}` | HTTP Basic credentials |
| `{"digest": {"username": "...", "password": "..."}}` | HTTP Digest, answering the server's challenge |
| `{"bearer": "..."}` | `Authorization: Bearer <token>` |
| `"module:func"` or `{"name": "module:func", "kwargs": {...}}` | whatever `httpx.Auth` your function returns |
| `false` (request only) | nothing — a public endpoint in an authenticated scenario |

Values take templates. The scenario's `auth` resolves once against the scenario substitutions, so a token an earlier stage **saved** goes in the request's own `auth`: `"auth": {"bearer": "{{ token }}"}`. A bare string is always a user function's name — `"auth": "{{ token }}"` fails.

## Response steps

Response is a list of `verify` and `save` steps, executed in order (a dict of named steps works too). A step that fails ends the stage.

```json
"response": [
  {
    "verify": {
      "status": 200,
      "headers": { "content-type": { "contains": "application/json" } },
      "jmespath": {
        "data.name": "{{ user_name }}",
        "data.id": { "type": "integer" },
        "data.total": { "gt": 0, "le": "{{ budget }}" },
        "length(data.roles)": { "ge": 1 }
      },
      "body": { "schema": "./openapi.json#/components/schemas/User" }
    }
  },
  {
    "save": {
      "jmespath": { "user_id": "data.id", "total": "length(items)" }
    }
  },
  {
    "verify": {
      "expressions": ["{{ total > 0 and response.elapsed_ms < 500 }}"]
    }
  }
]
```

One verify step runs **all** its checks and reports every failure, in this order: `status`, `headers`, `jmespath`, `expressions`, `user_functions`, `body.schema`, then `body.contains`/`not_contains`/`matches`/`not_matches`. Put checks in one step to see all their failures together, and in separate steps when a later one only makes sense once an earlier one held.

**Verify fields:**
- `status` — one code (100–599), a class `"1xx"`…`"5xx"`, or a non-empty list of either, any of which passes: `["2xx", 304]`.
- `headers` — a plain string is an **exact, full-string** match (`"application/json"` fails against `application/json; charset=utf-8`). For partial/pattern checks use a matcher object with any of `contains`, `not_contains`, `matches`, `not_matches` (regex, `re.search`); an absent header counts as `""`.
- `jmespath` — assert on the decoded JSON body. Each key is a JMESPath expression; its value is the JSON value it must equal (`true` is not `1`, `"42"` is not `42`) or a matcher object whose every key must hold: `eq`, `ne`, `gt`, `ge`, `lt`, `le`, `contains`, `not_contains`, `matches`, `not_matches`, `type` (`string`/`number`/`integer`/`boolean`/`array`/`object`/`null`), `length` (the exact count; for "at least one" check `"length(items)": {"ge": 1}`). **Values and matcher operands take templates**, so a value saved earlier is compared in place — `"id": "{{ order_id }}"`, no save-then-`expressions` detour; only the key is never rendered. An object here is always a matcher, so compare with an object through `eq`: `"meta": {"eq": {"page": 1}}`. A missing path gives `null`.
- `expressions` — `{{ }}` templates that must each evaluate to a real bool. Use them for logic across values (two body fields against each other, a sum, a value against `response` metadata); one body value against a literal or a saved name is a `jmespath` check.
- `user_functions` — `"module:func"` or `{"name": ..., "kwargs": {...}}`; each is called with the `httpx.Response` (plus kwargs) and returns a bool. It runs even when an earlier check of the step failed, so it must not assume a good response.
- `body.schema` — an inline JSON Schema (standard JSON Schema, verbatim: `$include`/`$merge` are **not** resolved inside it — `HTTPCHAIN028`), or a schema **file path** relative to the scenario file, optionally ending in a JSON pointer to one schema inside the document (an OpenAPI 3.1 component: `"./openapi.json#/components/schemas/User"`). `format` is enforced, and a `$ref` resolves within the document or to a local file, never over the network.
- `body.contains` / `not_contains` / `matches` / `not_matches` — lists of substrings or regexes checked against the body text.

**Save types** (one per step):
- `{"jmespath": {"name": "expr"}}` - extract values from the JSON body via JMESPath
- `{"regex": {"csrf": "name=\"csrf\" value=\"([^\"]+)\""}}` - extract from a body that is not JSON: group 1 of the first match (the whole match without groups). As an object: `{"pattern": "id=(\\d+)", "group": 1, "all": true}` — `group` by number or name, `all` for a list of every match. No match fails the step.
- `{"substitutions": [...]}` - compute values using template expressions
- `{"user_functions": [...]}` - call Python functions (given the `httpx.Response`) that each return a dict of names to save; `validate` can't see those names, so later uses draw an expected `HTTPCHAIN003`

**What templates can see in response steps:** the context (saved variables, fixtures, substitutions) plus a reserved **`response` metadata namespace** — `response.status`, `response.reason`, `response.headers` (case-insensitive), `response.elapsed_ms`: `"{{ response.status == 200 }}"`, or save a header with `{"save": {"substitutions": [{"vars": {"etag": "{{ response.headers['etag'] }}"}}]}}`. The namespace exists only in response steps, and a name of your own called `response` is shadowed there (`HTTPCHAIN027`). The response **body** is not in it: check it with `verify.jmespath`, or `save` a value and use the saved name. A value `save`d by a step is available to *later* steps and stages, never to the request that produced it.

## Template expressions

Use `{{ expr }}` syntax in any **value** (keys are never rendered — `HTTPCHAIN029`). A template that is the whole value keeps the value's type; inside text it renders as text. One template holds one Python expression.

**Available context:** all saved variables, fixture values, and substitution results.

**Built-in functions:** `len`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `range`, `zip`, `enumerate`, `bool`, `int`, `float`, `str`, `dict`, `list`, `tuple`, `set`, `uuid4()`, `rand()`, `randint(top)`, `env('VAR', default)`, `get('name', default)`, `exists('name')`, and helpers for time (`now()`, `timestamp()`), encoding (`b64encode`, `json_dumps`, …), URLs (`urlencode`, `quote`) and hashing (`sha256`, `hmac_sha256`, …) — signatures in [`references/templates.md`](references/templates.md).

- `env`, `get` and `exists` take the **name as a quoted string** — `exists(token)` passes the variable's *value*.
- Call a function to get its value: a bare `{{ now }}` or `{{ uuid4 }}` is the function itself and fails the stage (`HTTPCHAIN035`).
- **JSON literals:** `true`, `false`, `null` map to Python `True`, `False`, `None`.
- **Objects** read by attribute or by key: `{{ user.id }}`, `{{ user['id'] }}`. Use the key form for a key that is no Python name or starts with `_` (`{{ doc['_id'] }}`), and for a saved object's key named like a dict method (`{{ order['items'] }}`).
- **Literal braces:** `\{{` sends `{{` and opens no template — written `"\\{{name}}"` in the JSON file.
- A template that renders to `null` on an optional check or setting fails the stage rather than switching it off.
- The engine refuses a statement, an assignment (`=` written for `==`), a lambda, an attribute starting with `_`, and `.format()` (`HTTPCHAIN037`/`038`).

## References

Load these as the task needs them:

- [`references/composition.md`](references/composition.md) — substitutions, `$include` / `$merge` / `$ref`, parametrize, parallel execution (collected saves, load thresholds, stats).
- [`references/flow-control.md`](references/flow-control.md) — `retry` and polling, `skip_if`, `always_run`, stages a mark may skip, pytest-xdist rules.
- [`references/templates.md`](references/templates.md) — every built-in function, reading objects, literal braces, what the engine refuses, `null` rendering.
- [`references/connections.md`](references/connections.md) — connection stages for non-HTTP protocols: `connections`, the `connection` step, `expect`, adapters.
- [`references/diagnostics.md`](references/diagnostics.md) — every `HTTPCHAINxxx` code with its severity and fix.
- [`references/example.md`](references/example.md) — a complete multi-stage scenario, end to end.

## Validate your scenario

**Always validate after authoring or editing** — no server or network needed. Run it through the project's environment so user-function imports resolve against the project's code:

```bash
"<test-python>" -B -m pytest_httpchain.cli validate test_<name>.http.json
```

Use the actual scenario path, including `.jsonc` on package ≥0.16, where `validate` also takes a directory and checks every scenario pytest would collect under it (`validate tests/`). Never use `uvx` or a package-manager run command to repair a missing environment during validation.

It checks structure plus semantics a JSON Schema cannot, each with a stable `HTTPCHAINxxx` code and a severity. **Errors** fail `validate`; **warnings** pass it (`OK with warnings`) unless `--strict`; `HTTPCHAIN025` is **info** and never affects the result. Common ones (all of them in [`references/diagnostics.md`](references/diagnostics.md)):

- `HTTPCHAIN000` (error) — the scenario does not fit the model: an unknown key, a wrong type, a `jmespath` object that is no matcher. The message gives the path.
- `HTTPCHAIN003` (warning) — a `{{ var }}` that is never defined, saved, or provided as a fixture (likely a typo), or one saved only by a stage that may skip (read it with `get('name', default)`).
- `HTTPCHAIN004` (warning) — a variable used **before** it is saved or defined: saved by a later stage or a later step of the same response, or used in a stage's request when it is only saved in that same stage's response.
- `HTTPCHAIN006` (warning) — a `verify` step that asserts nothing.
- `HTTPCHAIN007` / `HTTPCHAIN008` (errors) — a body, header or `jmespath` matcher whose `contains`/`not_contains` (or `matches`/`not_matches`) name the same value, which can never pass.
- `HTTPCHAIN012` (error) — `$include`/`$merge`/`$ref` failed to resolve, including a sibling `Merge conflict` (see `references/composition.md`).
- `HTTPCHAIN034` (error) — a relative URL in a scenario whose `client` sets no `base_url`.
- `HTTPCHAIN035` (warning) — a built-in function used without calling it (`{{ now }}`).
- `HTTPCHAIN037` (warning) / `HTTPCHAIN038` (error) — a template the engine refuses from its text alone; the error form is one resolved before any stage runs.

A warning is still a finding, not a pass — most flag something that won't behave as written at runtime. Add `--format json` for machine-readable output. The same checks run automatically during `pytest --collect-only`.

For a deeper check that imports your `module:func` references and connection adapters (confirming they resolve and their signatures match — including the injected `response`, or `message` on a connection stage) and verifies referenced files and body schemas, add `--deep` (optionally `--syspath <dir>` for import roots, `--strict` to fail on warnings). Deep findings (`HTTPCHAIN020`–`024`, `046`) are always **warnings**, so an unimportable function still exits 0 without `--strict` — read the output. `--deep` imports your code, so it **must** run in the project environment, not via `uvx`:

```bash
"<test-python>" -B -m pytest_httpchain.cli validate --deep --syspath . test_<name>.http.json
```

Fix every reported issue and re-validate until clean. Then hand the user the run command (`"<test-python>" -m pytest -k <name>`, with the actual interpreter path) — do **not** run the suite yourself; firing the live HTTP calls is the user's call, not this skill's. For a whole-suite health check, use `pytest-httpchain-doctor`.

## Inspect (read-only, pytest-httpchain ≥0.5.0)

Four CLI commands explain a scenario without running it — all static, no network:

Invoke these through the selected interpreter as `"<test-python>" -B -m pytest_httpchain.cli <subcommand> ...`; the short command names below describe their behavior.

- `pytest-httpchain resolve <file>` — print the scenario with every `$ref`/`$include`/`$merge` inlined, as strict JSON. See what a composed, multi-file scenario actually becomes.
- `pytest-httpchain show <file>` — summarize stages and variable data-flow: per stage, what it `saves` and `consumes`, which earlier stage produced each consumed value, and its `skip_if`. A connection stage shows `CONNECTION <name>` and its actions. Add `--format json` for machine output.
- `pytest-httpchain graph <file>` — emit a Mermaid flowchart of the stage data-flow (`--direction LR` for wide chains); edges out of a stage that may skip, and connection edges, are dotted. Render it to show the user how a scenario chains.
- `pytest-httpchain schema > scenario.schema.json` — emit the scenario JSON Schema of the installed version (stdout only; `-o` was removed in 0.8.0) for editor autocomplete/validation, and to settle a field name this skill does not cover.

`show` and `graph` need the scenario to load — run `validate` first if they error; `resolve` works even when a scenario doesn't fully validate.

## Import recorded traffic (pytest-httpchain ≥0.16)

`import` writes a starter scenario from traffic the user already has. It sends nothing, validates what it writes, and replaces secrets (tokens, passwords, cookies) with `{{ env('...') }}` placeholders that it lists on stderr.

```bash
"<test-python>" -B -m pytest_httpchain.cli import har session.har -o tests/test_checkout.http.json
"<test-python>" -B -m pytest_httpchain.cli import curl -o tests/test_orders.http.json - < "<curl-file>"
```

- **Never paste a curl command into the shell line.** It is the user's text, with quotes, `$(...)` and usually a token in it. Write it unchanged to a new file of its own under a temporary directory with the file-writing tool, feed that file on stdin as above (`-` reads stdin; several commands make a stage each), then delete that one file.
- `-o` refuses an existing file unless `--force`; ask before overwriting a scenario. Without `-o` the scenario goes to stdout.
- `import har` leaves out static assets (`--all` keeps them); `--include REGEX` / `--exclude REGEX` filter entries by URL.
- The result is a starting point: each request is a stage verifying only its status (`2xx` for curl). Add the `save` steps that chain the stages and the `verify.jmespath` checks the test is for, then validate as usual. Tell the user which environment variables the scenario now reads.
