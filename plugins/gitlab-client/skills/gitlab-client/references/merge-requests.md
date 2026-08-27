# Merge requests (GitLab 15.x)

MRs are addressed by project-scoped **iid** (the `!142` number), never the global id. Reads are free; **create, update, comment, and resolve are confirm-first** — show the project, iid, exact text, and the command, and send only after an explicit yes.

## Find MRs

```bash
gl api GET /projects/:project/merge_requests state=opened order_by=updated_at --fields iid,title,author.username,source_branch,draft,detailed_merge_status,web_url
```

Filters: `source_branch=`, `target_branch=`, `author_username=`, `reviewer_username=`, `search=`, `wip=yes|no`. There is **no `draft=` filter on 15.x** — use `wip=`.

"The MR for my branch":

```bash
gl api GET /projects/:project/merge_requests source_branch=$(git branch --show-current) state=opened --fields iid,title,detailed_merge_status,web_url
```

## Read one

```bash
gl api GET /projects/:project/merge_requests/<iid> --fields iid,title,description,state,draft,detailed_merge_status,has_conflicts,source_branch,target_branch,head_pipeline.id,head_pipeline.status,head_pipeline.web_url,diff_refs,changes_count,reviewers,labels,web_url
```

- Use `detailed_merge_status` (**15.6+**; it is `null` on earlier 15.x — then read the deprecated `merge_status`: `can_be_merged` / `cannot_be_merged` / `unchecked`); `head_pipeline`, not `pipeline`.
- `changes_count` is a **string**, capped at `"1000+"`.
- `diff_refs` is empty for a few seconds after creation — retry before building a diff-note `position` from it.

## Diff

```bash
gl diff <iid> --files        # changed files first
gl diff <iid> --file path    # then only the files that matter
# uses /diffs (15.7+); on older 15.x gl falls back to the deprecated /changes — same output, plus a warning when the server set overflow
```

A file the server refused to render (over diff limits) appears as `[diff omitted by server …]` — read it via the raw files API or locally instead. Commits on the MR:

```bash
gl api GET /projects/:project/merge_requests/<iid>/commits --fields short_id,title
```

## Discussions vs notes

Read discussions — every comment lives here; `individual_note=true` marks a plain comment, everything else is a thread:

```bash
gl api GET /projects/:project/merge_requests/<iid>/discussions --all --fields id,individual_note,notes.0.author.username,notes.0.body,notes.0.resolvable,notes.0.resolved,notes.0.position.new_path,notes.0.position.new_line
```

Unresolved threads = `notes.0.resolvable && !notes.0.resolved`. For the full back-and-forth use `--fields id,notes`.

Writes — **confirm first**, one confirmation per distinct payload:

```bash
# new top-level comment (Notes API — cannot reply to a thread)
gl api POST /projects/:project/merge_requests/<iid>/notes body='…'

# new thread on a diff line — SHAs come from the MR's diff_refs
gl api POST /projects/:project/merge_requests/<iid>/discussions body='…' position:='{"position_type":"text","base_sha":"…","start_sha":"…","head_sha":"…","old_path":"p","new_path":"p","new_line":42}'

# reply to a thread (discussion id is a 40-hex string)
gl api POST /projects/:project/merge_requests/<iid>/discussions/<40-hex id>/notes body='…'

# resolve a thread (Developer, or the MR author)
gl api PUT /projects/:project/merge_requests/<iid>/discussions/<id> resolved:=true
```

## Create

Gather before proposing anything: branch pushed? target branch? title? description? draft? labels? reviewers? Then — **confirm first**:

```bash
gl api POST /projects/:project/merge_requests source_branch=feature/x target_branch=main title='Draft: Add X' description='…' remove_source_branch:=true squash:=true labels=backend,needs-review reviewer_ids:='[12]'
```

- Draft is the **`Draft:` title prefix** — there is no boolean, and `WIP:` is gone (removed 14.8).
- Reviewer ids — only assignable people (`/users` shows everyone):
  ```bash
  gl api GET /projects/:project/members/all query=jane --fields id,username,name,access_level
  ```
- Labels — validate names first: `gl api GET /projects/:project/labels search=… --fields name`.
- **409** = an open MR for that source branch already exists — find it with `source_branch=`.
- Default to **one** reviewer unless verified on the instance: multiple assignees is Premium, and multiple `reviewer_ids` on an unlicensed EE is unverified.

## Update

**Confirm first.** At least one attribute is required; send only the fields being changed:

```bash
gl api PUT /projects/:project/merge_requests/<iid> title='…'
gl api PUT /projects/:project/merge_requests/<iid> description='…'
gl api PUT /projects/:project/merge_requests/<iid> add_labels=x remove_labels=y
gl api PUT /projects/:project/merge_requests/<iid> reviewer_ids:='[12]'
gl api PUT /projects/:project/merge_requests/<iid> state_event=close   # or reopen
gl api PUT /projects/:project/merge_requests/<iid> target_branch=main
```

- Use `add_labels`/`remove_labels`, **never `labels=`** — it replaces the whole set.
- `reviewer_ids:='[]'` **unsets all** reviewers; omit the field to keep them.
- Mark ready = a title update that removes the `Draft:` prefix.

## Pipelines for an MR

Prefer `head_pipeline` from the MR read — it is the full object. The list endpoint is minimal (`id`, `sha`, `ref`, `status` only):

```bash
gl api GET /projects/:project/merge_requests/<iid>/pipelines --fields id,sha,ref,status
```
