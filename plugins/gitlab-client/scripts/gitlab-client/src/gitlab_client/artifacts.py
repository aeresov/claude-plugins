# SPDX-License-Identifier: AGPL-3.0-only
"""Job artifacts: archive download/list/extract and single-file fetch, by job id or by ref+job.

15.11 has no endpoint that lists an archive's contents, so listing means downloading the zip.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .errors import GlError, HttpError
from .http import Client, encode_path_segment

BY_REF_HINT = " (on GitLab 15.x the by-ref route only looks at the single latest *successful* pipeline of that branch/tag, and SHAs are not accepted — use a job id instead)"


def encode_artifact_path(p: str) -> str:
    return "/".join(encode_path_segment(seg) for seg in p.strip("/").split("/") if seg)


def cache_path(cache_dir: Path, job_id: int) -> Path:
    return cache_dir / f"job-{job_id}-artifacts.zip"


def _route(project_id: int, *, job_id: int | None, ref: str | None, job: str | None, by_job: str, by_ref: str) -> tuple[str, dict | None]:
    if job_id is not None:
        return f"/projects/{project_id}/jobs/{job_id}/artifacts{by_job}", None
    if not ref or not job:
        raise GlError("artifacts need a job id, or --ref REF --job NAME")
    return f"/projects/{project_id}/jobs/artifacts/{encode_path_segment(ref)}{by_ref}", {"job": job}


def _with_hint(e: HttpError, job_id: int | None) -> HttpError:
    if e.status == 404 and job_id is None:
        return HttpError(e.status, e.method, e.url, e.detail + BY_REF_HINT)
    return e


def download_archive(client: Client, project_id: int, dest: Path, *, job_id: int | None = None, ref: str | None = None, job: str | None = None) -> int:
    path, query = _route(project_id, job_id=job_id, ref=ref, job=job, by_job="", by_ref="/download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "wb") as fh:
            resp = client.request("GET", path, query=query, accept_json=False, stream_to=fh)
    except HttpError as e:
        dest.unlink(missing_ok=True)
        raise _with_hint(e, job_id) from None
    return resp.bytes_written


def fetch_file(client: Client, project_id: int, artifact_path: str, *, job_id: int | None = None, ref: str | None = None, job: str | None = None) -> bytes:
    enc = "/" + encode_artifact_path(artifact_path)
    path, query = _route(project_id, job_id=job_id, ref=ref, job=job, by_job=enc, by_ref="/raw" + enc)
    try:
        return client.request("GET", path, query=query, accept_json=False).body
    except HttpError as e:
        raise _with_hint(e, job_id) from None


def list_archive(path: Path) -> list[tuple[int, str]]:
    with zipfile.ZipFile(path) as zf:
        return [(info.file_size, info.filename) for info in zf.infolist() if not info.is_dir()]


def extract_archive(path: Path, dest: Path) -> int:
    """Extract into dest, refusing any entry that would land outside it. Returns the file count."""
    dest = dest.resolve()
    count = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise GlError(f"refusing to extract {info.filename!r}: it escapes {dest}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count
