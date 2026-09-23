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

Substitutions can appear at scenario level (global) or stage level (local).

A `functions` entry binds a **callable** — it isn't invoked when the substitution runs. Call it in a template: `"Authorization": "Bearer {{ generate_token() }}"` (a bare `{{ generate_token }}` renders the function object).

## References ($include / $merge / $ref)

Split scenarios across files. `$include` and `$merge` are preferred (they avoid editor conflicts with JSON-Schema's `$ref`); `$ref` is the legacy spelling. All three behave identically — the referenced content is **additively** deep-merged with any sibling properties.

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
- **Scalars** must be equal. A sibling `"method": "POST"` over a referenced `"GET"` is a conflict, and so is `null` against any other value (it is not an escape hatch). Mixing types at one path is a conflict too.

To vary a value per use, leave it **out** of the shared fragment (or reference a sub-node that omits it) and set it in each sibling.

Directives are **not** resolved inside an inline `verify.body.schema` (it's standard JSON Schema, verbatim — `HTTPCHAIN028`); to share a schema, use the file-path form `"schema": "./schemas/user.json"`.

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
