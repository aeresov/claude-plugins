# 15.11 compatibility

Routes and parameters from newer GitLab docs that fail or lie on 15.11, and deprecated fields that still appear but shouldn't be read. When a call 404s or a parameter seems ignored, check here before retrying. `gl version` warns when the instance isn't 15.x.

## Do not use on 15.11

Each arrived after 15.11 — the route 404s (`{"error": …}`) or the parameter is ignored/rejected.

| Don't use | Arrived | 15.11 alternative |
|---|---|---|
| `unidiff=true` on diff endpoints | 18.4 | `gl diff` renders unified text itself |
| `collapsed` / `too_large` per-file diff flags | 18.4 | an empty `diff` string is the only over-limit signal |
| `inputs` on `POST /projects/:project/pipeline` | 17.10 | `variables:='[{"key":"K","value":"V"}]'` |
| `job_inputs` on job retry/play | 18.10 | `job_variables_attributes:='[{"key":"K","value":"V"}]'` — and only on `/play`; retry takes no variables |
| `GET …/pipelines/<id>/trigger_jobs` | 19.2 | `gl api GET /projects/:project/pipelines/<id>/bridges` |
| `GET …/jobs/<id>/artifacts/tree` | 18.8 | download the archive: `gl artifacts <job_id> --list` |
| `search_recent_successful_pipelines` on artifacts-by-ref | 18.7 | the job-id routes: `gl artifacts <job_id> …` |
| `draft=` MR list filter | 19.0 | `wip=yes` / `wip=no` |
| `name=` pipeline list filter | behind a disabled feature flag on 15.11 | filter on `ref=`, `status=`, `source=`, `sha=` |
| `source=parent_pipeline` pipeline filter | 17.0 | `gl api GET /projects/:project/pipelines/<id>/bridges` → `downstream_pipeline.id` |
| `HEAD` as a ref shortcut on file endpoints | after 15.11 | pass the branch name (`ref=main`) |

## Deprecated but present — read the new field

Both old and new appear in 15.11 responses; the old ones are already deprecated and can disagree with reality. Always read (or send) the right-hand column.

| Deprecated | Use instead |
|---|---|
| `merge_status` | `detailed_merge_status` |
| `merged_by` | `merge_user` |
| `work_in_progress` | `draft` |
| `reference` | `references` |
| `GET …/merge_requests/<iid>/changes` | `GET …/merge_requests/<iid>/diffs` (paginated). Call `/changes?access_raw_diffs=true` only to detect `overflow` |
| MR `pipeline` field | `head_pipeline` — the full pipeline object, including `status` and `web_url` |
| `tag_list` | `topics` |
| `confidential` param on notes | `internal` (renamed in 16.0; both accepted on 15.11) |
