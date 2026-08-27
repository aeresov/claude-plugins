# Safety perimeter

What `gl` will and won't send, the confirmation Claude shows first, and how to read the errors. Exit codes: 0 ok · 1 HTTP/network · 2 config/usage · 3 refused by the write policy.

## The allow-list

POST/PUT are permitted **only** on these paths — enforced in `gl` itself, not by convention. Anything else exits 3 with `refused by gitlab-client write policy: <method> <path> (see safety-perimeter.md)` **before** project resolution, `token_cmd`, or any network call.

```
POST /projects/:id/merge_requests
PUT  /projects/:id/merge_requests/:iid                 # title, description, labels, reviewers, state_event close|reopen …
POST /projects/:id/merge_requests/:iid/notes
POST /projects/:id/merge_requests/:iid/discussions
POST /projects/:id/merge_requests/:iid/discussions/:did/notes
PUT  /projects/:id/merge_requests/:iid/discussions/:did  # resolved=true|false
POST /projects/:id/pipeline                            # trigger (note: singular)
POST /projects/:id/pipelines/:pid/retry
POST /projects/:id/pipelines/:pid/cancel
POST /projects/:id/jobs/:jid/retry
POST /projects/:id/jobs/:jid/cancel
POST /projects/:id/jobs/:jid/play
POST /projects/:id/jobs/:jid/artifacts/keep
```

- `DELETE` and `PATCH` do not exist in `gl` at all — the parser accepts only `GET | POST | PUT`.
- The path is matched exactly as it will be sent: anything that wouldn't round-trip — a `#`, whitespace, control or non-ASCII characters, or a `.`/`..` segment — is refused on every verb. URL-encode file paths and branch names (`%2F`, `%20`) instead.
- A `sudo` parameter is refused in code on **every** verb, whether passed as `sudo=…`, in a GET query, or inside a `--json` body.
- There is no override flag. Don't work around a refusal with `curl`; name the operation and hand it back to the user.

## Why each refused class is refused

| Class | Reason |
|---|---|
| merge, approve/unapprove | Authority — sign-off belongs to a human, in the UI |
| rebase | Rewrites history on the remote branch |
| delete anything, erase job logs | Irreversible |
| repository writes (branches, tags, files, commits) | git does them locally, with a diff to review before push |
| project/group/member/variable/hook/protected-branch settings, users, tokens | Blast radius far beyond one MR or pipeline |
| `sudo` | Impersonation — actions must be attributable to the token's owner |

## Confirmation format

Before any confirm-class write (create/update MR, note, discussion, reply, resolve), show the target as `project!iid`, the payload text **verbatim**, and the exact command; send only after an explicit yes. One confirmation per distinct payload, even after "just do it".

```
About to POST a comment on platform/api!142 (thread 6a1f…):
> Looks good — the retry handles the timeout case now.
Command: gl api POST /projects/:project/merge_requests/142/discussions/6a1f…/notes body='…'
Send it?
```

## Error semantics

| Status | Meaning |
|---|---|
| **404** | Ambiguous by design: the resource is missing **or** exists but the token can't see it. Also check: wrong path (typo, 15.11-missing route — see v15-compat), or a reverse proxy that decoded the `%2F` in an encoded file/project path. `gl` annotates this. |
| **403** `insufficient_scope` | The token lacks a scope — `gl` names it (writes need `api`; `read_api` is read-only). Fix the token, not the call. |
| **403** other | The *user's* role or a rule: below Developer for retry/cancel/play, or a protected branch/environment. |
| **401** | Token rejected — revoked, expired, or `token_cmd` emitted the wrong thing. Run `/gitlab-client:doctor`. |
| **409** | Duplicate — typically "an MR for this source branch already exists"; find it instead of retrying. |
| **429** | Rate limit. `gl` already honoured `Retry-After` (≤ 30 s) and retried once before failing; the body may be **plain text**, not JSON — `gl` handles either. Don't loop. |

## What never enters the transcript

- **The token.** `gl` runs `token_cmd` internally; its stdout is never printed, even in errors (stderr is). Never run `token_cmd` yourself.
- **The settings files.** Never `cat`/`Read` `~/.claude/gitlab-client.local.md` or `.claude/gitlab-client.local.md` — `gl` reads them.
- **Unbounded job logs.** Always a bounded view (`--sections`, `--section`, `--tail`, `--head`, `--grep`); `--tail 0` warns with the byte count first.
