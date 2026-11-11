# Complete example: a multi-stage API test

Loaded on demand from `SKILL.md`. One end-to-end scenario showing stage chaining,
saves feeding later stages, and verify steps together.

## Complete example: multi-stage API test

Needs pytest-httpchain ≥0.16 (`client`, built-in `auth`, `verify.jmespath`, status lists, `retry`).

```json
{
  "description": "Order lifecycle: log in, create an order, wait until it ships, clean up.",
  "substitutions": [
    { "vars": { "api_root": "{{ env('API_ROOT', 'http://localhost:8000/v1') }}" } }
  ],
  "client": {
    "base_url": "{{ api_root }}",
    "headers": { "Accept": "application/json" },
    "timeout": 10
  },
  "stages": [
    {
      "name": "login",
      "request": {
        "url": "/login",
        "method": "POST",
        "body": { "json": { "user": "demo", "password": "{{ env('DEMO_PASSWORD') }}" } }
      },
      "response": [
        { "verify": { "status": 200, "jmespath": { "token_type": "Bearer", "access_token": { "type": "string" } } } },
        { "save": { "jmespath": { "token": "access_token" } } }
      ]
    },
    {
      "name": "create order",
      "request": {
        "url": "/orders",
        "method": "POST",
        "auth": { "bearer": "{{ token }}" },
        "body": { "json": { "sku": "A-1", "qty": 2 } }
      },
      "response": [
        {
          "verify": {
            "status": [201, 202],
            "headers": { "Content-Type": { "contains": "application/json" } },
            "jmespath": { "qty": 2, "id": { "type": "integer" } }
          }
        },
        { "save": { "jmespath": { "order_id": "id" } } }
      ]
    },
    {
      "name": "wait until shipped",
      "retry": { "attempts": 10, "delay": 0.5, "backoff": 2, "max_delay": 5, "on": "verify" },
      "request": { "url": "/orders/{{ order_id }}", "auth": { "bearer": "{{ token }}" } },
      "response": [
        { "verify": { "status": 200, "jmespath": { "state": "shipped", "total": { "gt": 0 } } } }
      ]
    },
    {
      "name": "delete order",
      "always_run": "{{ exists('order_id') }}",
      "request": { "url": "/orders/{{ order_id }}", "method": "DELETE", "auth": { "bearer": "{{ token }}" } },
      "response": [
        { "verify": { "status": ["2xx", 404] } }
      ]
    }
  ]
}
```

What it shows:

- **`client.base_url`** from the environment, so every stage URL is relative and the scenario moves between environments without edits.
- **Secrets from `env()`**, never inline.
- **Chaining:** `login` saves `token`, used by each later request's own `auth` (the scenario-level `auth` resolves before any stage, so it cannot hold a saved token); `create order` saves `order_id`.
- **`verify.jmespath`** asserts on the body in place; `save` keeps only what a later stage needs.
- **`retry`** polls a `GET` — the `POST` that creates the order is a stage of its own, never retried.
- **`always_run` with `exists()`** cleans up after a failure only when there is something to delete, and its status list accepts an order that is already gone.

`show` prints its data-flow:

```text
1 · login    POST /login
    saves:    token
2 · create order    POST /orders
    saves:    order_id
    consumes: token (from #1 login)
3 · wait until shipped    GET /orders/{{ order_id }}
    consumes: order_id (from #2 create order), token (from #1 login)
4 · delete order    DELETE /orders/{{ order_id }}
    consumes: order_id (from #2 create order), token (from #1 login)
```
