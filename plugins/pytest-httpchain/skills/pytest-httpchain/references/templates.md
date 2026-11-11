# Templates: built-in functions, objects, literal braces, what the engine refuses

Loaded on demand from `SKILL.md`. The essentials (context, `{{ expr }}` in values only,
quoted names for `env`/`get`/`exists`) stay in `SKILL.md`; this file is the detail.
Version gates: the time, encoding, URL and hashing functions, the `\{{` escape and
reading a `vars` object by key need pytest-httpchain ≥0.16; the MessagePack and bytes
helpers ≥0.18; `sha1()` ≥0.19.

## Built-in functions

| Call | Returns |
|------|---------|
| `str()`, `int()`, `float()`, `bool()`, `list()`, `dict()`, `tuple()`, `set()` | Python's conversions |
| `len()`, `min()`, `max()`, `sum()`, `abs()`, `round()`, `sorted()`, `range()`, `zip()`, `enumerate()` | Python's built-ins |
| `true`, `false`, `null` | the JSON literals, as Python `True`, `False`, `None` |
| `uuid4()` | a fresh UUID string, new on every call |
| `rand()` | a random float, `0 <= x < 1` |
| `randint(top)` | a random int, `0 <= n < top` (one argument) |
| `env('VAR', default)` | an environment variable, `default` (else `null`) when unset |
| `get('name', default)` | the context value `name`, `default` (else `null`) when it is not defined |
| `exists('name')` | whether `name` is defined in the context |
| `now()` | the current UTC time, ISO 8601 with offset and microseconds: `2026-09-27T12:34:56.789012+00:00` |
| `now('%Y-%m-%d')` | the current UTC time formatted with `strftime` (`%s` is refused: use `timestamp()`) |
| `timestamp()`, `timestamp_ms()` | Unix time in whole seconds / milliseconds, as a **number** |
| `b64encode(value, urlsafe=false)` | base64 text of `value` (text is UTF-8 encoded first) |
| `b64decode(value, urlsafe=false)` | the UTF-8 text base64 `value` encodes; padding optional |
| `b64decode_bytes(value, urlsafe=false)` | the same, as bytes that need not be text |
| `hex_bytes(text)` | bytes from hexadecimal text — a MessagePack binary field |
| `hexencode(bytes)` | lowercase hexadecimal text of bytes |
| `msgpack_pack(value)` | `value` packed as MessagePack bytes, for a `bytes` body or a query parameter |
| `json_dumps(value)` | `value` as JSON text (a `vars` object as the object it is) |
| `json_loads(text)` | the JSON value `text` holds, objects as dicts |
| `urlencode(obj)` | a query string, encoded as `params` sends one: a list repeats its key |
| `quote(text, safe='')` | `text` percent-encoded for one path segment, `/` included unless listed in `safe` |
| `sha256(value)`, `sha1(value)`, `md5(value)` | the hex digest of text (as UTF-8) or bytes — a number fails: `sha256(str(order_id))` |
| `hmac_sha256(key, message, encoding='hex')` | HMAC-SHA256 as hex, or as base64 with `'base64'` |

Rules that bite:

- **Call it.** `{{ now }}`, `{{ env }}` or `str(timestamp)` hands over the function, not its value: the stage fails (`Uncalled function ... call it: now()`) and `validate` warns (`HTTPCHAIN035`).
- **Every call reads the clock or the dice again.** To use one moment or one id in several places, bind it once: `{"vars": {"sent_at": "{{ now() }}", "key": "{{ uuid4() }}"}}`. Scenario `substitutions` bind once per scenario; a stage's bind once per stage, before the first attempt of a `retry`.
- **A header value is text.** `"X-Timestamp": "{{ timestamp() }}"` is a number and fails; write `"{{ str(timestamp()) }}"` or put the call inside text (`"t={{ timestamp() }}"`). Numbers are fine in `params` and a JSON body.
- **Your names come first.** A variable, fixture, parameter, save or function substitution named like a built-in wins over it where it is in scope; `get()` and `exists()` always stay the built-ins. Out of scope the built-in silently takes its place (`HTTPCHAIN036`), so don't name a save `timestamp`, `max` or `len` without need.
- **`env`, `get`, `exists` take the name as a quoted string.** `get(token)` looks up a name equal to the token's value.

Signing a request, the signed text sent as the body so the two are the same bytes:

```json
{
  "substitutions": [
    { "vars": { "sent_at": "{{ str(timestamp()) }}", "payload": { "order": 42, "qty": 1 } } },
    { "vars": { "body_text": "{{ json_dumps(payload) }}" } }
  ],
  "stages": [
    {
      "name": "signed order",
      "request": {
        "url": "https://api.example.com/orders",
        "method": "POST",
        "headers": {
          "Content-Type": "application/json",
          "X-Timestamp": "{{ sent_at }}",
          "X-Signature": "{{ hmac_sha256(env('API_SECRET', ''), sent_at + '.' + body_text) }}"
        },
        "body": { "text": "{{ body_text }}" }
      },
      "response": [{ "verify": { "status": 201 } }]
    }
  ]
}
```

## Reading objects

An object reads by attribute or by key, at any depth and inside lists: `{{ user.name }}`,
`{{ user['name'] }}`, `{{ user.address.city }}`, `{{ items[0]['id'] }}`. It also takes
`'key' in user`, `len(user)`, `user.keys()`, `user.values()`, `user.items()`,
`user.get('nick', 'anon')`, and a comprehension over its keys (`{{ [k for k in user] }}`).

| Where the object comes from | It is | Mind |
|-----------------------------|-------|------|
| a `vars` substitution | a namespace | Attribute and key both read its keys. Interpolated into text it reads `namespace(...)`: send it whole as a value, or through `json_dumps()`. It never equals a dict: compare `json_loads(json_dumps(user))`. |
| a JMESPath `save`, a fixture's dict, a `combinations` parameter, `json_loads()` | a dict | An attribute reads a dict **method** before a key: for a saved `{"items": 3}`, `{{ saved.items }}` is the method. Read such a key (`items`, `keys`, `values`, `get`, `copy`, `pop`, `update`…) by key: `saved['items']`. |
| the `response` namespace of a response step | metadata | `response.status`, `response['status']`; `response.headers['etag']` is case-insensitive. |

- A key that is no Python name, or starts with `_`, reads only by key: `{{ trace['X-Request-Id'] }}`, `{{ doc['_id'] }}`.
- A missing key or attribute fails the stage, naming it. For one that may be missing use `user.get('nick')` or `'nick' in user`; for a name that may be missing, `get('name', default)`.
- An empty object is false in a condition, a template `always_run` included. Test `len(opts) > 0` or `'key' in opts` when that is what is meant.
- Where a field takes a whole object, one template naming it stands for that object: `"json": "{{ payload }}"`, GraphQL `variables`, `verify.body.schema`, a header matcher, the `combinations` of a `parametrize` or `parallel.foreach` step.

## Literal braces

`{{` in a value opens a template. To send the braces themselves (a Handlebars or Mustache
payload, a `{{placeholder}}` the server expects), put a backslash before them — doubled in
the JSON file, where a backslash escapes itself:

```json
{
  "stages": [
    {
      "name": "store a template",
      "request": {
        "url": "https://api.example.com/templates",
        "method": "POST",
        "headers": { "X-Template": "\\{{name}}" },
        "body": { "json": { "subject": "Hello \\{{user.name}}", "body": "\\{{#each items}}\\{{this}}\\{{/each}}" } }
      },
      "response": [{ "verify": { "status": 201 } }]
    }
  ]
}
```

- The escaped text runs to the first `}}` after it, so each tag of a payload takes its own backslash.
- To keep a real backslash before a template (a Windows path), double it once more: `"C:\\\\{{ dir }}"` renders `C:\` and the value.
- A key and a `functions` kwarg are never rendered: they keep the backslash, and `validate` warns (`HTTPCHAIN029`, `HTTPCHAIN030`).
- A pattern (`save.regex`, a `matches` operand) takes templates too, so literal braces in one are escaped as regex escapes them: `"\\{\\{\\s*(\\w+)\\s*\\}\\}"`.

## What the engine refuses

One template is one expression, evaluated by a restricted engine. These fail the stage,
and `validate` reports them from the text alone — `HTTPCHAIN037` (warning) in a stage,
`HTTPCHAIN038` (error) in a scenario-level template or a parametrize value, where nothing
would run:

| Written | Write instead |
|---------|---------------|
| `{{ user.active = True }}` (an assignment), `+=`, `:=` | `{{ user.active == True }}` |
| `{{ a; b }}` (two statements) | one expression per template |
| `{{ {'a': 1}}}` (the dict's `}` runs into `}}`) | `{{ {'a': 1} }}` |
| `doc._id` (an attribute starting with `_` or `func_`) | `doc['_id']` |
| `'{}'.format(x)` | `f'id-{x}'` or `'id-' + str(x)` |
| a lambda, `yield`, `await` | a user function |
| `{x for x in items}` (a set comprehension) | `set(x for x in items)` |
| `fns[0]()` (calling anything but a name or an attribute) | bind the function to a name |

A comprehension is limited to `httpchain_max_comprehension_length` items (ini option, default 50000).

## Templates that render to `null`

`get()` without a default, or a JMESPath save of a key the response did not have, gives
`null`. On an optional check or setting that would read as "not declared", so the stage
fails instead, naming the field and the template:

```text
'verify.headers.Content-Type.contains' was declared as '{{ expected_ct }}' but rendered to None, which would silently disable it
```

That covers `verify.status`, `verify.body.schema`, header matcher fields, `jmespath` matcher
operands, `request.auth` and its credentials, a `save.regex` `group`, a multipart file's
`filename`/`content_type`, `parallel.calls_per_sec` and thresholds, `retry.max_delay`, and
(failing scenario initialization) `ssl.cert`, `client.base_url`, `client.proxy` and the
pool limits. Two places keep `null` as a value: a `jmespath` value written as a template
compares with `null`, and `"json": null` sends the JSON document `null`. To compare with
`null`, write `null`.
