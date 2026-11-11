# Composition: substitutions, $include / $merge / $ref, parametrize, parallel

Loaded on demand from `SKILL.md`. Everything here is about assembling a scenario from
reusable parts and expanding it across cases — the per-stage request/response syntax
stays in `SKILL.md`.

## Substitutions

Define variables before stages run:

```json
"substitutions": [
  { "vars": { "base_url": "https://api.example.com", "count": "{{ 2 + 3 }}" } },
  { "functions": { "generate_token": "mymodule:create_jwt" } }
]
```

Substitutions can appear at scenario level (global) or stage level (local). Steps run in
order, so a later step may read what an earlier one defined (the other way round is
`HTTPCHAIN004`). A dict of named steps works in place of the list; the names only organize.

A `functions` entry binds a **callable** — it isn't invoked when the substitution runs. Call it in a template: `"Authorization": "Bearer {{ generate_token() }}"` (a bare `{{ generate_token }}` renders the function object). Its `kwargs` are passed unrendered (`HTTPCHAIN030`).

Scenario-level substitutions resolve once, before any fixture or save exists, so they may
read only each other, `env()` and the other built-ins (`HTTPCHAIN016`/`017` otherwise).

## References ($include / $merge / $ref)

Split scenarios across files. `$include` and `$merge` are preferred (they avoid editor conflicts with JSON-Schema's `$ref`); `$ref` is the legacy spelling. All three behave identically — the referenced content is **additively** deep-merged with any sibling properties. A `#/json/pointer` after the path selects one node of the file.

```json
{
  "request": {
    "$include": "common.json#/requests/get_user"
  }
}
```

Sibling properties **add to** the referenced content — they never override it:

```json
{
  "$include": "base_request.json",
  "headers": { "X-Request-Id": "abc-123" }
}
```

Merge rules (a violation fails loading with `HTTPCHAIN012: … Merge conflict at <path>`):
- **Objects** merge recursively — new keys are added, shared keys merged by these rules.
- **Arrays** concatenate — referenced elements first, then the sibling's.
- **Scalars** must be equal, as JSON (`true` is not `1`). A sibling `"method": "POST"` over a referenced `"GET"` is a conflict, and so is `null` against any other value (it is not an escape hatch). Mixing types at one path is a conflict too.
- **Three values merge whole**, like a scalar, because concatenating them would loosen a shared check: a `verify.status` list, a stage's `retry.on`, and one `verify.jmespath` expression's expectation (matcher included). An equal one is kept; a different one is a conflict. Different `jmespath` expressions still merge side by side.

To vary a value per use, leave it **out** of the shared fragment (or reference a sub-node that omits it) and set it in each sibling. To extend one shared `jmespath` matcher, write the reference at the expectation itself: `"price": {"$merge": "common.json#/checks/verify/jmespath/price", "lt": 100}`.

Path rules:
- A relative path is looked up next to the **referencing file** first, then under the root (pytest's rootdir, or `--root-path`). A file found under both draws `HTTPCHAIN026`.
- At most `httpchain_ref_parent_traversal_depth` `../` segments (ini option, default 3), never an absolute path, and never outside the root.
- A referenced file may be `.json` or `.jsonc`, with comments.

Directives are **not** resolved inside an inline `verify.body.schema` (it's standard JSON Schema, verbatim — `HTTPCHAIN028` for `$include`/`$merge`). A JSON Schema `$ref` inside it does resolve, within the schema or to a local file relative to the scenario's directory; to share a whole schema, use the file-path form `"schema": "./schemas/user.json"`, optionally with a pointer (`"./openapi.json#/components/schemas/User"`).

Run `resolve <file>` to see what a composed scenario becomes.

## Parametrize

Run a stage with different inputs:

```json
"parametrize": [
  {
    "individual": { "user_id": [1, 2, 3] },
    "ids": ["user-one", "user-two", "user-three"]
  }
]
```

Or use combinations:

```json
"parametrize": [
  {
    "combinations": [
      { "method": "GET", "expected": 200 },
      { "method": "DELETE", "expected": 403 }
    ]
  }
]
```

- Several steps multiply: 3 values × 2 values is 6 runs.
- A value list, or a whole `combinations` list, may be one template over scenario `vars` (`"individual": {"user": "{{ test_users }}"}`); it resolves at collection (`HTTPCHAIN025`, info), so it can read scenario substitutions only.
- A template that draws at random or reads the clock (`uuid4()`, `now()`) needs explicit `ids` under pytest-xdist.
- Every run shares the chain: its saves, its cookies. To run the **whole scenario** once per value, each run starting fresh, list a `class`-scoped fixture with `params` in the scenario's `fixtures` instead.

## Parallel execution

Execute requests concurrently for load testing:

```json
"parallel": {
  "repeat": 100,
  "max_concurrency": 10,
  "calls_per_sec": 50,
  "max_rate_limit_delay": 60
}
```

`max_concurrency` defaults to 10; `calls_per_sec` (optional) is shared by this stage's iterations only; `max_rate_limit_delay` (default 60) is how many seconds a request waits for a rate-limit slot before failing.

Or iterate over parameter sets in parallel:

```json
"parallel": {
  "foreach": [{ "individual": { "id": [1, 2, 3, 4, 5] } }],
  "max_concurrency": 5
}
```

How a parallel stage behaves:
- The first failing iteration cancels the rest and fails the stage, and saves are **all or nothing**: a stage with a failed iteration commits none.
- Iterations do not see one another's saves. By default the saves merge into one value per name, the highest iteration index winning.
- More than 100 requests in flight against an HTTP/2 server needs `"client": {"http2": false}`.
- Not available on a `connection` stage (`HTTPCHAIN041`).

Three more options need pytest-httpchain ≥0.16:

| Option | Meaning |
|--------|---------|
| `collect_saves` | `true` keeps every iteration's saves: each saved name becomes a list, one entry per iteration in iteration order (`null` where an iteration did not save it) |
| `thresholds` | Limits checked once every iteration has ended: `min_success_ratio` (0–1), `min_rps`, `max_mean_ms`, `max_p50_ms`, `max_p95_ms`, `max_p99_ms`. A `min_success_ratio` below 1 lets the stage run on after failures and keeps the passed iterations' saves |
| `stats_as` | Save the stage's stats as an object under this name for later stages: `iterations`, `passed`, `failed`, `success_ratio`, `wall_ms`, `rps`, `completed_rps`, `min_ms`, `mean_ms`, `p50_ms`, `p95_ms`, `p99_ms`, `max_ms`. Must not be a name the stage's response saves (`HTTPCHAIN040`) |

Create several resources, load one, then clean every one of them up:

```json
{
  "client": { "base_url": "https://api.example.com" },
  "stages": [
    {
      "name": "create resources",
      "parallel": { "foreach": [{ "individual": { "name": ["alpha", "beta", "gamma"] } }], "collect_saves": true },
      "request": { "url": "/resources", "method": "POST", "body": { "json": { "name": "{{ name }}" } } },
      "response": [{ "verify": { "status": 201 } }, { "save": { "jmespath": { "created_ids": "id" } } }]
    },
    {
      "name": "sustained load",
      "parallel": {
        "repeat": 200,
        "max_concurrency": 20,
        "thresholds": { "min_success_ratio": 0.99, "max_p95_ms": 300 },
        "stats_as": "load"
      },
      "request": { "url": "/resources" },
      "response": [{ "verify": { "status": 200 } }]
    },
    {
      "name": "report",
      "request": { "url": "/health" },
      "response": [{ "verify": { "status": 200, "expressions": ["{{ load.p95_ms < 300 }}"] } }]
    },
    {
      "name": "delete resources",
      "always_run": "{{ exists('created_ids') }}",
      "parallel": { "foreach": [{ "individual": { "id": "{{ created_ids }}" } }] },
      "request": { "url": "/resources/{{ id }}", "method": "DELETE" },
      "response": [{ "verify": { "status": [204, 404] } }]
    }
  ]
}
```

The total iterations of one stage are capped by the `httpchain_max_parallel_iterations` ini option (default 10000). `parallel` runs iterations *within* one stage; running whole scenarios in parallel is pytest-xdist's job (see `flow-control.md`).
