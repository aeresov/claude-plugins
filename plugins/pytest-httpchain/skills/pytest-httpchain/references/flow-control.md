# Flow control: retry and polling, `skip_if`, `always_run`, marks that skip, pytest-xdist

Loaded on demand from `SKILL.md`. Everything here decides *whether* and *how often* a
stage runs, and what that leaves for the stages after it. `skip_if` and `retry` need
pytest-httpchain ≥0.16.

## How a chain runs

- Stages run in order. A stage that **fails** discards its saves and aborts the chain: every later stage is skipped ("Flow aborted") unless its `always_run` lets it through.
- A stage that is **skipped** — by `skip_if`, or by its own `skip`/`skipif`/`xfail` mark — is no failure: the chain goes on, but that stage saved nothing.
- So a name can be missing in a later stage for two reasons, and `validate` reports the second (`HTTPCHAIN003`, naming the `skip_if` or the mark). Read a name that may be missing with `get('name', default)`, or guard the stage with `exists('name')`.

## `always_run` — cleanup after a failure

`true`, or a template evaluated with Python truthiness at the moment the stage would be
skipped after a failure. In scope: fixtures, parametrize parameters, scenario
substitutions and what earlier stages saved — not the stage's own `substitutions`.

```json
{
  "client": { "base_url": "https://api.example.com" },
  "stages": [
    {
      "name": "create",
      "request": { "url": "/resources", "method": "POST" },
      "response": [{ "verify": { "status": 201 } }, { "save": { "jmespath": { "resource_id": "id" } } }]
    },
    {
      "name": "check",
      "request": { "url": "/resources/{{ resource_id }}" },
      "response": [{ "verify": { "status": 200 } }]
    },
    {
      "name": "cleanup",
      "always_run": "{{ exists('resource_id') }}",
      "request": { "url": "/resources/{{ resource_id }}", "method": "DELETE" },
      "response": [{ "verify": { "status": [204, 404] } }]
    }
  ]
}
```

`cleanup` runs after a failure only if `create` got far enough to save `resource_id`.
Truthiness, not a boolean: a saved string `"false"` is truthy, and an empty object is false.

## `skip_if` — decide at run time

A `skip` mark is fixed at collection; `skip_if` reads what is known only when the stage is
about to run — a setting, a fixture, an earlier save.

```json
{
  "client": { "base_url": "https://api.example.com" },
  "substitutions": [{ "vars": { "target": "{{ env('TARGET_ENV', 'dev') }}" } }],
  "stages": [
    {
      "name": "login",
      "request": { "url": "/login", "method": "POST" },
      "response": [{ "verify": { "status": 200 } }, { "save": { "jmespath": { "token": "token", "mfa_required": "mfa" } } }]
    },
    {
      "name": "confirm mfa",
      "skip_if": "{{ not mfa_required }}",
      "request": { "url": "/mfa", "method": "POST", "auth": { "bearer": "{{ token }}" } },
      "response": [{ "verify": { "status": 200 } }]
    },
    {
      "name": "reset data",
      "skip_if": "{{ target == 'prod' }}",
      "request": { "url": "/reset", "method": "POST", "auth": { "bearer": "{{ token }}" } },
      "response": [{ "verify": { "status": 204 } }]
    }
  ]
}
```

- **Must be a real boolean.** A string, number or `null` fails the stage (`skip_if must evaluate to bool, got str from '{{ flag }}'`). Compare (`{{ flag == 'yes' }}`) or convert (`{{ bool(count) }}`).
- **Scope:** what the request sees — fixtures, parametrize parameters, scenario substitutions, earlier saves, the stage's own `substitutions` (which run first) — but not `parallel.foreach` parameters and not `response`.
- A skipped stage sends nothing and saves nothing. A later stage that reads a name only such a stage saves gets `HTTPCHAIN003`: read it with `get('name', default)`. A stage that never skips saving the same name settles it.
- In a chain a failure aborted, `skip_if` still applies to a stage `always_run` let through.

## Marks that skip

A stage's own `skip`, `skipif(...)` or `xfail(...)` mark has the same effect on later
stages as `skip_if`: pytest reports the stage skipped or xfailed, the chain goes on, and
its saves are never made. `validate` (≥0.17) reports a later direct read as `HTTPCHAIN003`,
naming the mark. A scenario-level mark applies to every stage, so it does not count.

Supported forms in `marks`: `"skip"`, `"skip(reason='...')"`, `"skipif(...)"`, `"xfail"`,
`"usefixtures('name')"` (runs the fixture; its value is **not** available to templates —
list it in `fixtures` for that). An invalid marker expression is `HTTPCHAIN019`.

## `retry` — polling until a stage passes

```json
{
  "client": { "base_url": "https://api.example.com" },
  "stages": [
    {
      "name": "submit",
      "request": { "url": "/jobs", "method": "POST", "body": { "json": { "report": "monthly" } } },
      "response": [{ "verify": { "status": 202 } }, { "save": { "jmespath": { "job_id": "id" } } }]
    },
    {
      "name": "wait for job",
      "retry": { "attempts": 10, "delay": 0.5, "backoff": 2, "max_delay": 5, "on": "verify" },
      "request": { "url": "/jobs/{{ job_id }}" },
      "response": [
        { "verify": { "status": 200, "jmespath": { "status": "done" } } },
        { "save": { "jmespath": { "result_url": "result.url" } } }
      ]
    },
    {
      "name": "download",
      "request": { "url": "{{ result_url }}" },
      "response": [{ "verify": { "status": 200 } }]
    }
  ]
}
```

| Option | Default | Meaning |
|--------|---------|---------|
| `attempts` | required | Attempts in all, the first included (at least 1) |
| `delay` | `1` | Seconds before the second attempt |
| `backoff` | `1` | Factor the wait is multiplied by after each attempt (at least 1) |
| `max_delay` | `null` | Cap on a single wait, in seconds; `null` for none |
| `on` | all three | Which failures make another attempt: `"verify"`, `"save"`, `"request"`, or a list |

The wait before attempt *n* + 1 is `delay × backoff^(n − 1)`, capped at `max_delay`.

- **`verify`** — a verify step's checks failed (status, header, `jmespath`, expression, body, a verify function returning `False`). **`save`** — a save step could not take its values (a body that is not JSON, a regex without a match). **`request`** — a timeout or a connection that failed. A response is never a `request` failure, whatever its status: to retry on `503`, verify the status and retry on `verify`.
- **Each attempt renders the request anew** (a fresh `uuid4()`, a current `now()`) and runs every response step in a context of its own; only the attempt that passes commits its saves. The stage's `substitutions`, `skip_if`, `always_run` and the `retry` settings are decided once, before the first attempt.
- **Never retried:** a template that cannot render, a user function that crashed or cannot be imported, an unreadable body schema, `pytest.skip()`/`xfail()`/`fail()` from a user function.
- **Do not retry a non-idempotent request blindly.** Start the job in a stage of its own and retry the `GET` that polls it, as above. To retry a `POST`, use `"on": "verify"` (only after the server answered) or send an idempotency key bound once in the stage's `substitutions`: `{"vars": {"key": "{{ uuid4() }}"}}` with `"Idempotency-Key": "{{ key }}"`.
- The settings except `on` take templates (`"attempts": "{{ int(env('POLL_ATTEMPTS', '30')) }}"`), rendered once in `skip_if`'s scope. `true`/`false` is refused in any of them.
- A user function says "not yet" by returning `False` (verify) or raising `SaveError` (save), and "never" with `VerificationError(..., retryable=False)` / `SaveError(..., retryable=False)` from `pytest_httpchain.errors`, which ends the stage at once.
- In a `parallel` stage each iteration makes its own attempts. A sibling `on` beside a `$merge`d `retry` is a merge conflict, never concatenated.

## pytest-xdist

A scenario's stages are one chain on shared state, so they must run on one worker, in order.

- Supported: `--dist loadscope`, `--dist loadfile`, `--dist loadgroup`. Plain `-n 4` (`--dist load`), `each` and `worksteal` are rejected at collection.
- An `xdist_group` mark goes in the **scenario's** `marks` (`"marks": ["xdist_group('db')"]`), to keep several scenarios on one worker. On a stage it is `HTTPCHAIN031`, and a group name with a `]` after its last `@` is `HTTPCHAIN033`.
- A stage name with `::` is `HTTPCHAIN032`: rename it (`Users: list`).
- A parametrize step whose values are drawn at random or read the clock needs explicit `ids`, or the workers collect different tests.
