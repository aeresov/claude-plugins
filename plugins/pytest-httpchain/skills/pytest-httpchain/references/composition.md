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

## References ($include / $merge / $ref)

Split scenarios across files. `$include` and `$merge` are preferred (they avoid editor conflicts with JSON-Schema's `$ref`); `$ref` is the legacy spelling. All three behave identically — the referenced content is deep-merged with any sibling properties.

```json
{
  "request": {
    "$include": "common.json#/requests/get_user"
  }
}
```

Sibling properties are deep-merged with the referenced content:

```json
{
  "$include": "base_request.json",
  "headers": { "X-Custom": "override" }
}
```

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
  "calls_per_sec": 50
}
```

Or iterate over parameter sets in parallel:

```json
"parallel": {
  "foreach": [{ "individual": { "id": [1, 2, 3, 4, 5] } }],
  "max_concurrency": 5
}
```
