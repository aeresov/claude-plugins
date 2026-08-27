# Repo browsing (GitLab 15.11)

Read-only repository access through `gl`. All paths take `:project` (expands to the URL-encoded project path). Always pass `--fields` on lists.

## Project

Resolved project (id, path, default branch, how it was resolved):

```bash
gl project
```

Full lookup when you need more:

```bash
gl api GET /projects/:project --fields id,path_with_namespace,default_branch,web_url,permissions
```

`permissions` shows the token owner's access level. 404 here means *not found or not visible* — ask for the path, don't guess.

## Tree

```bash
gl api GET /projects/:project/repository/tree path=src ref=main recursive:=true --all --fields path,type
```

Since 15.0 this endpoint is **keyset-paginated only**; `gl` follows the `Link` headers automatically — just use `--all` (`--max N` to cap). A missing `path` returns `200 []`, **not** 404 — an empty array does not prove the ref exists.

## Files

`ref` is **required** on 15.11 for every files call. Encode the *whole* file path with `%2F` (e.g. `src/app.py` → `src%2Fapp.py`).

Metadata first when size is unknown:

```bash
gl api GET /projects/:project/repository/files/src%2Fapp.py ref=main --fields size,encoding,blob_id,last_commit_id
```

`content` in the JSON is base64 — prefer the raw route:

```bash
gl api GET /projects/:project/repository/files/src%2Fapp.py/raw ref=main
gl api GET /projects/:project/repository/files/src%2Fapp.py/raw ref=main --out /tmp/app.py   # large: then Read the file
```

An LFS-tracked file comes back as the pointer text (`version https://git-lfs…`, `oid sha256:…`, `size N`) — and `size` on the JSON route is the pointer's size. Add `lfs:=true` to get the content (ignored for non-LFS files).

By blob sha (skips path encoding once `blob_id` is known):

```bash
gl api GET /projects/:project/repository/blobs/<sha>/raw
```

Blame — always pass the range (docs are contradictory on whether it's optional):

```bash
gl api GET /projects/:project/repository/files/src%2Fapp.py/blame ref=main 'range[start]=1' 'range[end]=80'
```

## Commits

```bash
gl api GET /projects/:project/repository/commits ref_name=main path=src since=2026-08-01T00:00:00Z --fields short_id,title,author_name,created_at
```

`ref_name` accepts a range: `ref_name=v1.0..main`. Don't rely on `x-total` counts.

Single commit (accepts a branch/tag name too; includes its latest pipeline):

```bash
gl api GET /projects/:project/repository/commits/<sha> --fields id,title,last_pipeline.status,stats
```

Its diff: `gl diff --commit <sha>`. Related lookups:

```bash
gl api GET /projects/:project/repository/commits/<sha>/refs             # branches/tags containing it
gl api GET /projects/:project/repository/commits/<sha>/merge_requests   # MRs that introduced it
gl api GET /projects/:project/repository/commits/<sha>/statuses         # CI statuses
```

## Compare

```bash
gl diff --range main..feature/x            # merge-base (three-dot) semantics
gl diff --range main..feature/x --straight # two-dot: literal diff between the tips
```

The response is unpaginated — a huge range comes back in one lump; if `compare_timeout` is true in the raw response, the server gave up and the diff is incomplete.

## Branches / tags

```bash
gl api GET /projects/:project/repository/branches search=^feature --all --fields name,commit.short_id,merged,protected
gl api GET /projects/:project/repository/tags order_by=version --fields name,commit.short_id,message
```

`search=^x` anchors at the start. `order_by=version` sorts tags semver-style (15.4+). Names containing `/` must be `%2F`-encoded when they appear *in the path* (`…/repository/branches/feature%2Fx`) — not as query values.

## Code search

```bash
gl api GET /projects/:project/search scope=blobs search="def resolve_project" --fields path,startline,data
```

On 15.11 the docs gate blob search behind **Premium + Elasticsearch**. If it 400s/403s — or returns nothing for a term you know exists — don't retry: fall back to tree (`recursive:=true`) + raw files + local grep, and remember the outcome for the rest of the turn.

## Archive

```bash
gl api GET /projects/:project/repository/archive.zip sha=main path=docs --out /tmp/docs.zip
```

`sha` is any ref or commit; `path` limits to a subdirectory. Rate-limited to **5 requests/min per project** — for a handful of files, fetch them raw instead.
