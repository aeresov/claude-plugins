# 15.x compatibility

Routes and parameters from newer GitLab docs that fail or lie on 15.x, things that only arrived part-way through 15.x, and deprecated fields that still appear but shouldn't be read. When a call 404s or a parameter seems ignored, check here before retrying. `gl version` warns when the instance isn't 15.x and notes when it predates 15.11.

## Introduced within 15.x — check the instance's minor first

Verified on 15.2.5 CE: the left column 404s / 400s / comes back `null` there.

| Needs | Since | Before that |
|---|---|---|
| `GET …/merge_requests/<iid>/diffs` | 15.7 | `gl diff` falls back to `GET …/merge_requests/<iid>/changes` automatically (`changes[]`, plus `overflow`) |
| `detailed_merge_status` on an MR | 15.6 | `null` — read `merge_status` (`can_be_merged` / `cannot_be_merged` / `unchecked`) |
| `GET /personal_access_tokens/self` | 15.5 | 401 — `/doctor` can't show scopes/expiry; check them in the GitLab UI |
| `enterprise` in `GET /metadata` | 15.6 | absent; a version string without `-ee` is CE |
| `order_by=version` on tags | 15.4 | 400 `order_by does not have a valid value` — sort client-side |
| `author=` on commits | 15.10 | ignored — filter on `author_name`/`author_email` client-side |
| keyset pagination on `GET /projects/:id/jobs` | 15.9 | offset paging (what `--all` uses there anyway) |
| `approved=` MR filter | 15.11 (feature-flagged) | not available |

## Do not use on 15.x

Each arrived after 15.11 — the route 404s (`{"error": …}`) or the parameter is ignored/rejected.

| Don't use | Arrived | 15.x alternative |
|---|---|---|
| `unidiff=true` on diff endpoints | 18.4 | `gl diff` renders unified text itself |
| `collapsed` / `too_large` per-file diff flags | 18.4 | an empty `diff` string is the only over-limit signal |
| `inputs` on `POST /projects/:project/pipeline` | 17.10 | `variables:='[{"key":"K","value":"V"}]'` |
| `job_inputs` on job retry/play | 18.10 | `job_variables_attributes:='[{"key":"K","value":"V"}]'` — and only on `/play`; retry takes no variables |
| `GET …/pipelines/<id>/trigger_jobs` | 19.2 | `gl api GET /projects/:project/pipelines/<id>/bridges` |
| `GET …/jobs/<id>/artifacts/tree` | 18.8 | download the archive: `gl artifacts <job_id> --list` |
| `search_recent_successful_pipelines` on artifacts-by-ref | 18.7 | the job-id routes: `gl artifacts <job_id> …` |
| `draft=` MR list filter | 19.0 | `wip=yes` / `wip=no` |
| `name=` pipeline list filter | behind a disabled feature flag on 15.x | filter on `ref=`, `status=`, `source=`, `sha=` |
| `source=parent_pipeline` to list child pipelines | accepted on 15.x but returns `[]` — child pipelines are excluded from the list until 17.0 | `gl api GET /projects/:project/pipelines/<id>/bridges` → `downstream_pipeline.id` |
| `HEAD` as a ref shortcut on file endpoints | after 15.11 | pass the branch name (`ref=main`) |

## Deprecated but present — read the new field

Both old and new appear in 15.x responses (where the new one exists — see the table above); the old ones are already deprecated and can disagree with reality. Always read (or send) the right-hand column.

| Deprecated | Use instead |
|---|---|
| `merge_status` | `detailed_merge_status` |
| `merged_by` | `merge_user` |
| `work_in_progress` | `draft` |
| `reference` | `references` |
| `GET …/merge_requests/<iid>/changes` | `GET …/merge_requests/<iid>/diffs` (paginated). Plain `/changes` (deprecated 15.7) is the one response that carries `overflow: true` when the instance diff limits truncated it; `access_raw_diffs=true` is the opposite lever — it pulls the *unlimited* diff from Gitaly (slow, can be huge) — use it only for a file `/diffs` returned empty, and stream it with `--out` |
| MR `pipeline` field | `head_pipeline` — the full pipeline object, including `status` and `web_url` |
| `tag_list` | `topics` |
| `confidential` / `internal` on notes | neither exists on **MR** notes in 15.x (they're issue/epic-only, out of scope here); `POST …/merge_requests/<iid>/notes` takes `body` only — don't send either |
