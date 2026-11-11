# Connection stages: non-HTTP protocols

Loaded on demand from `SKILL.md`. Needs pytest-httpchain ≥0.19. A `connection` stage
drives a long-lived, non-HTTP connection (a device's TCP link, a framed binary protocol,
pushed commands) through an **adapter**: a Python class in the user's project that opens
the connection, sends one message and decodes the next. The scenario only names it.

```json
{
  "client": { "base_url": "http://localhost:8000" },
  "connections": {
    "device": {
      "adapter": "adapters:LineJsonAdapter",
      "params": { "host": "127.0.0.1", "port": "{{ env('DEVICE_PORT', '9000') }}" }
    }
  },
  "stages": [
    {
      "name": "hello",
      "connection": { "name": "device", "open": true },
      "response": [{ "verify": { "jmespath": { "greeting": "hello" } } }]
    },
    {
      "name": "ping",
      "connection": {
        "name": "device",
        "send": { "op": "ping" },
        "expect": { "match": { "op": "pong", "reply_to": "{{ sent.seq }}" }, "timeout": 2 }
      },
      "response": [{ "save": { "jmespath": { "server_time": "time" } } }]
    },
    { "name": "flush", "connection": { "name": "device", "drain": true } },
    {
      "name": "trigger push",
      "request": { "url": "/devices/1/play", "method": "POST" },
      "response": [{ "verify": { "status": 202 } }]
    },
    {
      "name": "push arrives",
      "connection": { "name": "device", "expect": { "match": "op == 'play'", "timeout": 3 } },
      "response": [{ "verify": { "jmespath": { "track": { "type": "string" } } } }]
    },
    {
      "name": "bye",
      "always_run": true,
      "connection": { "name": "device", "send": { "op": "bye" }, "close": true }
    }
  ]
}
```

A stage has a `request` or a `connection`, never both. HTTP and connection stages mix in
one chain and read each other's saves.

## Declaring a connection

`connections` maps a name to its adapter:

| Option | Default | Meaning |
|--------|---------|---------|
| `adapter` | required | The adapter class as `module:Class`, imported like a user function |
| `params` | `{}` | Keyword arguments for the adapter's constructor; templates allowed |
| `connect_timeout` | `10` | Seconds, passed to the adapter's `connect()` |
| `inbox_limit` | `1000` | Most messages kept queued; older ones are dropped and counted |
| `description` | | Free text |

`params` render each time the connection opens. They may read scenario substitutions,
**scenario-level** `fixtures` (class-scoped or wider), and saves of stages before the
opening one — not the opening stage's own fixtures, parameters or substitutions.

## The connection step

| Option | Default | Meaning |
|--------|---------|---------|
| `name` | required | A key of `connections` (`HTTPCHAIN042` otherwise) |
| `open` | `false` | Connect now, reconnecting if open. Without an `expect`, what `connect()` returned is the stage's message |
| `drain` | `false` | Discard every message already queued |
| `send` | | One message (an object, or one template) for the adapter to send |
| `expect` | | Wait for a message (below) |
| `close` | `false` | Close the connection once the stage has run, pass or fail |

A step sets at least one of these, and they run in this order: open, drain, send, expect,
the response steps, close.

- The first stage that names a connection opens it implicitly, taking no message; write `open: true` only to check the handshake result or to connect again.
- After a `close: true`, a later stage must say `open: true` (`HTTPCHAIN044`); a closed connection is never reopened implicitly.
- Connections survive a failed stage, so an `always_run` stage can send a farewell; all are closed when the scenario ends. After a failure, an `always_run` stage whose connection is not open is skipped — it never connects just to say goodbye.

## `expect` — waiting for a message

Everything received is queued in arrival order. `expect` takes the oldest queued message
that matches. A stage that **sends** looks only at messages that arrived after its send; a
stage that sends nothing looks at the whole queue. Non-matching messages stay queued.

| Option | Default | Meaning |
|--------|---------|---------|
| `match` | any message | A JMESPath expression (a string, never rendered — no `{{` in it), taken when truthy: ``"header.action == 'DoCommand' && body.seq > `3`"``. Or an object of expectations written as in `verify.jmespath`, values rendered: `{"op": "pong", "body.items": {"length": 2}}` |
| `timeout` | `5` | Seconds to wait |
| `absent` | `false` | Pass if **no** message matches within `timeout`; fail at once on one that does |
| `closed` | `false` | Wait for the other side to end the connection; takes no `match` and no `absent` |

- **Push test:** open and `drain` the connection *before* the HTTP stage that triggers the push — a `drain` in the expecting stage would discard the very message it waits for.
- **Nothing should happen:** `{"expect": {"match": "op == 'play'", "absent": true, "timeout": 3}}` after the call that must not push.
- A timed-out `expect` fails the stage, listing the messages it considered and why each did not match.

## Checking the message

The message a stage took — the `expect` match, or what `connect()` returned on an
`open: true` stage without an `expect` — is what its response steps work on:

- `verify.jmespath`, `save.jmespath` and `verify.body.schema` read it as a decoded JSON body.
- Templates in the response steps read it as `message` (`{{ message.body.state == 'playing' }}`). A stage that sends also reads what the adapter's `send()` returned as `sent`, in its response steps and in its `expect.match` values. A name of your own called `message` or `sent` is shadowed there (`HTTPCHAIN045`).
- User functions are called with `message=` (a dict) in place of `response=`: `def ack_accepted(message: dict) -> bool`.

Refused on a connection stage (`HTTPCHAIN041`): `verify.status`, `verify.headers`,
`verify.body.contains`/`not_contains`/`matches`/`not_matches`, `save.regex`,
`response_codec`, `parallel`, and reading `response`. A stage that takes no message — no
`expect` and no `open: true`, or an `absent` expect — cannot run `verify.jmespath`,
`verify.body.schema`, `save.jmespath` or user functions (`HTTPCHAIN043`).

## Retry

`retry` works here: each attempt runs the step again from the top (reconnecting on
`open: true`, draining, sending and waiting anew). A timed-out `expect` is a `verify`
failure; an adapter that fails to connect or send with an `OSError`, `EOFError` or a
`RequestError(..., retryable=True)` is a `request` failure. Never retried: a step on a
connection that is not open without `open: true`, and an `absent` expect that saw its
message. For reconnect polling, combine `open: true` with `retry`.

## The adapter is the user's code

The scenario author does not invent an adapter: it is a class the project provides, like
a `module:func`. If it does not exist yet, say so and list it as an open question. Its
contract, which `validate --deep` checks (`HTTPCHAIN046`):

```python
class LineJsonAdapter:
    def __init__(self, host: str, port: int): ...          # the connection's rendered params
    def connect(self, timeout: float) -> dict | None: ...  # open + handshake; the result is the open stage's message
    def send(self, message: dict) -> dict | None: ...      # a returned dict is the stage's `sent`
    def receive(self, timeout: float) -> dict | None: ...  # next message, None on timeout; raise EOFError on a clean end
    def close(self) -> None: ...                           # must make a waiting receive() return
    def debug_info(self) -> str | None: ...                # optional: text for failure reports
```

`receive()` runs on a reader thread while `send()` runs on the test's: an adapter that
writes from both guards its writes with one lock. A message is a dict with string keys
holding JSON values or bytes. `pytest_httpchain.ProtocolAdapter` is the matching
`typing.Protocol`.

Connection stages write no HAR entries. A failing one gets a *Connection Exchange*
report section; the `httpchain_redact_message_keys` ini option hides listed keys' values in it.
