# `HTTPCHAINxxx` diagnostics: meaning, severity, fix

Loaded on demand from `SKILL.md`. Every finding of `validate` (and of pytest collection,
which runs the same checks) carries one of these codes. As of pytest-httpchain 0.19.0;
codes are append-only, so an older package simply lacks the later ones (`031`–`040` came
with 0.16, `041`–`046` with 0.19).

**Severity decides the outcome — never report a warning as an error, or the reverse.**

- **error** — the file is invalid: `validate` exits non-zero and pytest collection fails.
- **warning** — the file is valid (`OK with warnings`, exit 0) unless `--strict`. Still a finding: most mean the scenario will not behave as written.
- **info** — never affects the result, and is exempt from `--strict`.

| Code | Severity | Meaning | Fix |
|------|----------|---------|-----|
| `HTTPCHAIN000` | error | The scenario does not fit the model: an unknown or misspelled key, a wrong type, an invalid value | The message gives the path (`stages -> 0 -> request -> ...`). Check the key against `SKILL.md`, the installed version's gate, or the `schema` subcommand |
| `HTTPCHAIN001` | error | Duplicate stage names | Rename one |
| `HTTPCHAIN002` | error | A fixture and a variable share a name | Rename the variable |
| `HTTPCHAIN003` | warning | A name referenced but never defined, saved or provided as a fixture; or saved only by stages that may skip (`skip_if`, or a `skip`/`skipif`/`xfail` mark) | Fix the typo, define it, or read it with `get('name', default)`. Expected for a name only a `user_functions` save returns |
| `HTTPCHAIN004` | warning | A name used before it is saved or defined: by a later stage, a later step of the same response, a later substitution step, or the request of the stage whose response saves it | Reorder: a save reaches later steps and stages only |
| `HTTPCHAIN005` | warning | A stage has no verify step | Add one, or leave it for a deliberate cleanup stage and say so |
| `HTTPCHAIN006` | warning | A verify step asserts nothing | Add a check or remove the step |
| `HTTPCHAIN007` | error | `contains` and `not_contains` name the same value (body, header or `jmespath` matcher) | Remove the contradiction |
| `HTTPCHAIN008` | error | `matches` and `not_matches` name the same pattern | Remove the contradiction |
| `HTTPCHAIN009` | warning | A saved name is shadowed by a scenario-level fixture | Save under another name |
| `HTTPCHAIN010` | error | File not found | Fix the path given to the command |
| `HTTPCHAIN011` | error | Path is not a file | Name a file (a directory is searched on ≥0.16) |
| `HTTPCHAIN012` | error | `$include`/`$merge`/`$ref` did not resolve: a missing file, a bad pointer, a path outside the root or past the `../` limit, or a `Merge conflict at <path>` | Fix the path, or see the merge rules in `composition.md` |
| `HTTPCHAIN013` | warning | File extension is neither `.json` nor `.jsonc` | pytest would not collect it: rename |
| `HTTPCHAIN014` | error | Invalid JSON in the scenario or a file it includes: a syntax error, an unclosed `/*`, a duplicate key, bytes that are not UTF-8, `NaN`/`Infinity` | Fix at the line and column given; the message names an included file |
| `HTTPCHAIN015` | error | The file could not be parsed (nested too deeply) | Flatten it |
| `HTTPCHAIN016` | error | A fixture referenced in a scenario-level template (`substitutions`, `auth`, `ssl`, `client`) | Move the use into a stage, or read the value with `env()` |
| `HTTPCHAIN017` | error | A scenario-level template references a name the scenario `substitutions` do not define (a save, for instance) | Define it there, or move the use into a stage (a saved token goes in the request's `auth`) |
| `HTTPCHAIN018` | warning | A verify expression is not a `{{ }}` template | Wrap it: `"{{ total > 0 }}"` |
| `HTTPCHAIN019` | error | Invalid pytest marker expression in `marks` | Write it as pytest would: `"skip(reason='...')"` |
| `HTTPCHAIN020` | warning | `--deep`: a referenced file does not exist (`ssl`, a `binary` body, an uploaded file, a body schema file or a file its `$ref` names) | Fix the path (relative to the scenario file) or create the file |
| `HTTPCHAIN021` | warning | `--deep`: a body schema cannot be used — not JSON, a pointer to nothing, an invalid schema, a `$ref` that does not resolve | Fix the schema or its pointer |
| `HTTPCHAIN022` | warning | `--deep`: a user function cannot be imported | Fix `module:func`, or add its directory with `--syspath` |
| `HTTPCHAIN023` | warning | `--deep`: an unexpected argument is passed to a user function | Match `kwargs` to the signature |
| `HTTPCHAIN024` | warning | `--deep`: a required argument of a user function is missing | Add it to `kwargs` (verify/save functions also get `response`, or `message` on a connection stage) |
| `HTTPCHAIN025` | info | Template parametrize values force collection-time resolution | Nothing to fix |
| `HTTPCHAIN026` | warning | A reference path matches files under both lookup bases (the referencing file's directory and the root) | Rename one of the files |
| `HTTPCHAIN027` | warning | A name of your own called `response` is shadowed in response steps | Rename it |
| `HTTPCHAIN028` | warning | `$include`/`$merge` inside an inline `verify.body.schema`, where they are not resolved | Use a JSON Schema `$ref` (a local file resolves), or the schema-file form |
| `HTTPCHAIN029` | warning | A template, or an escaped `\{{`, in a dict **key**: keys are never rendered | Move the dynamic part into the value |
| `HTTPCHAIN030` | warning | A template, or an escaped `\{{`, in a `functions` substitution's `kwargs`, which are passed unrendered | Pass the value another way (a `vars` entry the function reads, or a call argument in the template) |
| `HTTPCHAIN031` | error | An `xdist_group` mark on a stage, naming a group the scenario does not declare | Move it to the scenario's `marks` |
| `HTTPCHAIN032` | error | A stage name contains `::` | Rename (`Users: list`) |
| `HTTPCHAIN033` | error | The scenario's `xdist_group` name has a `]` after its last `@` | Take the `]` out of the name |
| `HTTPCHAIN034` | error | A relative `request.url` while `client` sets no `base_url` | Set `client.base_url`, or write the URL absolute |
| `HTTPCHAIN035` | warning | A built-in function used without calling it (`{{ now }}`, `str(timestamp)`) | Call it: `now()` |
| `HTTPCHAIN036` | warning | A built-in's name the scenario also defines, used as a function where that definition is not in scope: the built-in runs instead, silently | Give your function or save a name of its own |
| `HTTPCHAIN037` | warning | A stage template the engine refuses from its text: a syntax error, two statements, an assignment, a lambda, an attribute such as `_id` or `format` | See *What the engine refuses* in `templates.md` |
| `HTTPCHAIN038` | error | The same in a template resolved before any stage runs: scenario-level, or a parametrize value | As `037` |
| `HTTPCHAIN039` | error | A directory given to `validate` holds no scenario file | Fix the path or the suffix (`--suffix`); doctor reads a sole `039` as an empty suite |
| `HTTPCHAIN040` | warning | A parallel stage's `stats_as` names a variable its own response saves too: the stats replace it | Pick another name |
| `HTTPCHAIN041` | error | A connection stage sets what only an HTTP stage has: `parallel`, `response_codec`, `verify.status`/`headers`, body text matchers, `save.regex`, or a read of `response` | Check the message with `verify.jmespath`, or move the check to an HTTP stage |
| `HTTPCHAIN042` | error | A stage's `connection.name` is not one of the scenario's `connections` | Declare it, or fix the name |
| `HTTPCHAIN043` | error | A connection stage that takes no message has a response step that reads one | Add an `expect` that takes a message, or move the step |
| `HTTPCHAIN044` | error | A stage uses a connection an earlier stage closed, without `open: true` | Set `open: true`, or drop the earlier `close: true` |
| `HTTPCHAIN045` | warning | A name of your own called `message` or `sent` is shadowed on a connection stage | Rename it |
| `HTTPCHAIN046` | warning | `--deep`: a connection's adapter cannot be used — not importable, a method missing or with the wrong arguments, a constructor that does not take the `params` | Fix the adapter class or the `params` |

## Deep checks

`HTTPCHAIN020`–`024` and `046` come only from `validate --deep`, which imports the user's
modules and touches the filesystem. They are always warnings: pair `--deep` with
`--strict` when they must fail the run.

## Silencing one code at collection

Warning-severity findings surface during pytest collection as
`pytest_httpchain.ScenarioValidationWarning`. A project that accepts one filters it — a
decision for the user, never a way to make an authored scenario look clean:

```ini
[pytest]
filterwarnings =
    ignore:.*HTTPCHAIN005.*:pytest_httpchain.ScenarioValidationWarning
```
