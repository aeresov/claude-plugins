# SPDX-License-Identifier: AGPL-3.0-only
import io
import zipfile

import pytest

from gitlab_client.artifacts import (
    BY_REF_HINT,
    download_archive,
    encode_artifact_path,
    extract_archive,
    fetch_file,
    list_archive,
)
from gitlab_client.errors import GlError, HttpError

PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "u"}


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


GOOD = {"reports/junit.xml": b"<testsuite/>", "dist/app.tar": b"x" * 10, "dist/": b""}
EVIL = {"ok.txt": b"fine", "../evil.txt": b"nope"}


def test_encode_artifact_path():
    assert encode_artifact_path("reports/a b.xml") == "reports/a%20b.xml"
    assert encode_artifact_path("/dir/x#1.txt/") == "dir/x%231.txt"


def test_list_and_extract(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip(GOOD))
    assert list_archive(archive) == [(12, "reports/junit.xml"), (10, "dist/app.tar")]
    dest = tmp_path / "out"
    assert extract_archive(archive, dest) == 2
    assert (dest / "reports/junit.xml").read_bytes() == b"<testsuite/>" and (dest / "dist").is_dir()


def test_extract_refuses_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(make_zip(EVIL))
    with pytest.raises(GlError, match="refusing to extract '../evil.txt'"):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_download_archive_by_job_and_by_ref(client, opener, tmp_path):
    opener.add(200, b"zipbytes")
    dest = tmp_path / "c" / "job-5-artifacts.zip"
    assert download_archive(client, 42, dest, job_id=5) == 8 and dest.read_bytes() == b"zipbytes"
    assert opener.last.full_url.endswith("/projects/42/jobs/5/artifacts") and opener.last.get_header("Accept") is None

    opener.add(200, b"zip2")
    assert download_archive(client, 42, dest, ref="release/1.0", job="build") == 4
    assert opener.last.full_url.endswith("/projects/42/jobs/artifacts/release%2F1.0/download?job=build")

    opener.add(404, {"message": "404 Not Found"})
    with pytest.raises(HttpError) as e:
        download_archive(client, 42, dest, ref="main", job="build")
    assert BY_REF_HINT in str(e.value) and not dest.exists()

    opener.add(404, {"message": "404 Not Found"})
    with pytest.raises(HttpError) as e:
        download_archive(client, 42, dest, job_id=5)
    assert BY_REF_HINT not in str(e.value)


def test_fetch_file(client, opener):
    opener.add(200, b"<testsuite/>")
    assert fetch_file(client, 42, "reports/junit.xml", job_id=5) == b"<testsuite/>"
    assert opener.last.full_url.endswith("/projects/42/jobs/5/artifacts/reports/junit.xml")
    opener.add(200, b"y")
    assert fetch_file(client, 42, "a b.txt", ref="main", job="build") == b"y"
    assert opener.last.full_url.endswith("/projects/42/jobs/artifacts/main/raw/a%20b.txt?job=build")


def test_gl_artifacts_cli(run_gl, opener, tmp_path):
    opener.add(200, PROJECT_JSON).add(200, make_zip(GOOD))
    code, out, err = run_gl("artifacts", "5", "--project", "group/proj", "--list")
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].endswith("job-5-artifacts.zip (" + str(len(make_zip(GOOD))) + " bytes)")
    assert lines[1:] == ["        12 reports/junit.xml", "        10 dist/app.tar"]

    opener.add(200, PROJECT_JSON)  # cached by job id: no download
    dest = tmp_path / "x"
    code, out, _ = run_gl("artifacts", "5", "--project", "group/proj", "--extract", str(dest))
    assert code == 0 and f"extracted 2 file(s) to {dest}" in out and (dest / "reports/junit.xml").exists()

    opener.add(200, PROJECT_JSON).add(200, b"<testsuite/>")
    code, out, _ = run_gl("artifacts", "5", "--project", "group/proj", "--file", "reports/junit.xml")
    assert code == 0 and out == "<testsuite/>"

    opener.add(200, PROJECT_JSON).add(200, make_zip(GOOD))
    code, out, _ = run_gl("artifacts", "--ref", "main", "--job", "build", "--project", "group/proj", "--list")
    assert code == 0 and "reports/junit.xml" in out
    opener.add(200, PROJECT_JSON).add(200, make_zip(GOOD))  # by-ref is never cached
    code, out, _ = run_gl("artifacts", "--ref", "main", "--job", "build", "--project", "group/proj", "--list")
    assert code == 0 and not opener.queue

    code, _, err = run_gl("artifacts", "--project", "group/proj")
    assert code == 2 and "JOB_ID" in err
    code, _, err = run_gl("artifacts", "--ref", "main", "--project", "group/proj")
    assert code == 2 and "--job" in err


def test_artifacts_rejects_job_name_with_job_id(run_gl):
    code, _, err = run_gl("artifacts", "5", "--job", "build", "--project", "group/proj")
    assert code == 2 and "cannot be combined with JOB_ID" in err
