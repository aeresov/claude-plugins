# Pipelines (GitLab 15.11)

CI/CD through `gl`. Reads are free; retry/cancel/play/trigger are **go-and-report** writes — run them, then report the resulting id, status, and `web_url`. No merging, no deleting, no log erasing (exit 3).

## Find the pipeline

Latest pipelines for a branch:

```bash
gl api GET /projects/:project/pipelines ref=main order_by=updated_at sort=desc per_page=5 --fields id,status,source,sha,created_at,web_url
```

Or just the newest one: `gl api GET /projects/:project/pipelines/latest ref=main`. Other list filters: `status=`, `source=`, `sha=`, `updated_after=`.

For an MR, don't list — read `head_pipeline` from the MR object (see merge-requests.md). Single pipeline:

```bash
gl api GET /projects/:project/pipelines/<id> --fields id,status,ref,sha,duration,web_url,user.username
```

**Child pipelines never appear in the list** on 15.11 — reach them through bridges (below). The single-pipeline and jobs endpoints do accept child ids.

## Jobs and stages

```bash
gl api GET /projects/:project/pipelines/<id>/jobs --all --fields id,name,stage,status,allow_failure,duration,web_url
```

- Failed only: add `scope[]=failed`. Superseded attempts: add `include_retried:=true`.
- There is no stages endpoint — group the jobs by `stage` yourself (they come back ordered by id desc, not stage order).

Bridges (trigger jobs → child / multi-project pipelines):

```bash
gl api GET /projects/:project/pipelines/<id>/bridges --fields id,name,status,downstream_pipeline.id,downstream_pipeline.status
```

Recurse with `downstream_pipeline.id` as an ordinary pipeline id.

## Logs

Never dump a log you haven't sized. Narrow in three steps:

```bash
gl log <job_id> --sections                                        # runner sections, line counts, durations
gl log <job_id> --section step_script --tail 80                   # the part that ran the script
gl log <job_id> --grep 'error|failed|exception|traceback' -C 3    # targeted search with context
```

`gl` caches the trace; add `--refresh` while the job is still running. Size caps: the runner truncates at 4 MB by default, the instance at 100 MB — a silent cut-off mid-log is usually the runner cap.

## Retry / cancel / play (go and report)

```bash
gl api POST /projects/:project/jobs/<id>/retry
gl api POST /projects/:project/jobs/<id>/cancel
gl api POST /projects/:project/jobs/<id>/play job_variables_attributes:='[{"key":"DEPLOY_ENV","value":"staging"}]'
gl api POST /projects/:project/pipelines/<id>/retry
gl api POST /projects/:project/pipelines/<id>/cancel
```

- Job retry creates a **new job id** (report it); the old job's status becomes `retried`. Bridge jobs can't be retried on 15.11 — retry the pipeline instead.
- `play` works on manual jobs only; `job_variables_attributes` is optional.
- Pipeline retry reruns the failed/canceled jobs and **keeps the same pipeline id**.
- Pipeline cancel may return 200 with nothing actually cancelled — re-fetch the status before reporting.
- 403 here = role below Developer, or a protected branch.

## Trigger (go and report)

```bash
gl api POST /projects/:project/pipeline ref=main variables:='[{"key":"RUN_E2E","value":"1"}]'
```

The path is **singular** (`/pipeline`, not `/pipelines`). Echo the ref and every variable back to the user when reporting. The new pipeline's `source` is `api`.

## Test reports

Needs jobs with `artifacts:reports:junit`. Summary first:

```bash
gl api GET /projects/:project/pipelines/<id>/test_report_summary --fields total
```

The full report is large — stream it to a file (`--out` and `--fields` don't combine) and grep it:

```bash
gl api GET /projects/:project/pipelines/<id>/test_report --out /tmp/report.json
grep -n '"status": "failed"' /tmp/report.json
```

## Artifacts

```bash
gl artifacts <job_id> --list                    # download archive (cached) and list entries
gl artifacts <job_id> --file reports/junit.xml  # one file, no archive download
gl artifacts <job_id> --extract DIR
```

By ref, when you don't have a job id:

```bash
gl artifacts --ref main --job build --list
```

`--ref` takes a branch or tag only (SHAs rejected) and searches only the **latest successful** pipeline — a red pipeline's artifacts need the job id. Stop expiry:

```bash
gl api POST /projects/:project/jobs/<id>/artifacts/keep
```

## Status vocabulary

Jobs: `created` `pending` `running` `failed` `success` `canceled` `skipped` `waiting_for_resource` `manual`. Pipelines add `preparing` and `scheduled`. Don't filter on values from newer GitLab versions — 15.11 rejects or ignores them.
