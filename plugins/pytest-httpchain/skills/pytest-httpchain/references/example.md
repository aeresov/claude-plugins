# Complete example: a multi-stage API test

Loaded on demand from `SKILL.md`. One end-to-end scenario showing stage chaining,
saves feeding later stages, and verify steps together.

## Complete example: multi-stage API test

```json
{
  "substitutions": [
    { "vars": { "base": "{{ env('API_URL', 'http://localhost:8000') }}" } }
  ],
  "stages": [
    {
      "name": "create user",
      "request": {
        "url": "{{ base }}/users",
        "method": "POST",
        "body": { "json": { "name": "Alice", "email": "alice@example.com" } }
      },
      "response": [
        { "verify": { "status": 201 } },
        { "save": { "jmespath": { "user_id": "id" } } }
      ]
    },
    {
      "name": "get user",
      "request": {
        "url": "{{ base }}/users/{{ user_id }}"
      },
      "response": [
        { "verify": { "status": 200 } },
        { "save": { "jmespath": { "name": "name" } } },
        { "verify": { "expressions": ["{{ name == 'Alice' }}"] } }
      ]
    },
    {
      "name": "delete user",
      "request": {
        "url": "{{ base }}/users/{{ user_id }}",
        "method": "DELETE"
      },
      "response": [
        { "verify": { "status": 204 } }
      ]
    }
  ]
}
```
