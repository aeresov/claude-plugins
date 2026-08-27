# SPDX-License-Identifier: AGPL-3.0-only
from gitlab_client.log import (
    clean_line,
    fetch_trace,
    grep,
    head,
    header_line,
    parse_trace,
    section_body,
    sections_summary,
    tail,
)

TRACE = (
    "\x1b[0KRunning with gitlab-runner 15.11.0\x1b[0;m\n"
    "section_start:1700000000:prepare_executor\r\x1b[0KPreparing the \"docker\" executor\x1b[0;m\n"
    "Using Docker executor with image python:3.11 ...\n"
    "section_end:1700000004:prepare_executor\r\x1b[0K\n"
    "section_start:1700000004:step_script[collapsed=true]\r\x1b[0K\x1b[32;1m$ pytest -q\x1b[0;m\n"
    "section_start:1700000005:download_deps\r\x1b[0KDownloading  10%\rDownloading  55%\rDownloading 100%\n"
    "section_end:1700000008:download_deps\r\x1b[0K\n"
    "FAILED tests/test_x.py::test_y - AssertionError\n"
    "\x1b[31;1mERROR: Job failed: exit code 1\x1b[0;m\n"
    "section_end:1700000031:step_script\r\x1b[0K\n"
    "section_start:1700000031:cleanup\r\x1b[0K\n"
    "Cleaning up project directory\n"
)
CLEAN = [
    "Running with gitlab-runner 15.11.0",
    'Preparing the "docker" executor',
    "Using Docker executor with image python:3.11 ...",
    "$ pytest -q",
    "Downloading 100%",
    "FAILED tests/test_x.py::test_y - AssertionError",
    "ERROR: Job failed: exit code 1",
    "Cleaning up project directory",
]
JOB = {"id": 5, "name": "test", "stage": "test", "status": "failed", "duration": 31.2, "web_url": "https://g/j/5"}
PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "u"}


def test_clean_line():
    assert clean_line("\x1b[32;1mgreen\x1b[0;m") == "green"
    assert clean_line("a\rb\rc") == "c"
    assert clean_line("keep\r") == "keep"
    assert clean_line("\x1b]0;title\x07text") == "text"
    assert clean_line("\x1b[32;1mgreen\x1b[0;m", strip_ansi=False) == "\x1b[32;1mgreen\x1b[0;m"


def test_parse_trace_sections_and_cleanup():
    lines, sections = parse_trace(TRACE)
    assert lines == CLEAN
    assert [(s.name, s.start_line, s.end_line, s.duration) for s in sections] == [
        ("prepare_executor", 1, 3, 4),
        ("step_script", 3, 7, 27),
        ("download_deps", 4, 5, 3),  # nested inside step_script
        ("cleanup", 7, 8, None),
    ]
    assert sections_summary(sections) == [
        "prepare_executor · 2 lines · 4s",
        "step_script · 4 lines · 27s",
        "download_deps · 1 lines · 3s",
        "cleanup · 1 lines · unterminated",
    ]
    assert section_body(lines, sections, "step_script") == CLEAN[3:7]
    assert section_body(lines, sections, "nope") is None


def test_parse_trace_raw_keeps_ansi():
    lines, sections = parse_trace(TRACE, strip_ansi=False)
    assert "\x1b[32;1m$ pytest -q" in lines[3]
    assert [s.name for s in sections] == ["prepare_executor", "step_script", "download_deps", "cleanup"]


def test_views():
    lines = [f"l{i}" for i in range(10)]
    assert tail(lines, 3) == ["l7", "l8", "l9"] and tail(lines, 0) == lines
    assert head(lines, 2) == ["l0", "l1"] and head(lines, 0) == lines
    assert grep(lines, "L[28]", context=1) == ["2: l1", "3: l2", "4: l3", "--", "8: l7", "9: l8", "10: l9"]
    assert grep(lines, "zzz") == []


def test_fetch_trace_caches_terminal_jobs(client, opener, tmp_path):
    cache = tmp_path / "c"
    opener.add(200, JOB).add(200, b"line1\nline2\n")
    job, path, size = fetch_trace(client, 42, 5, cache)
    assert job == JOB and path == cache / "job-5.log" and size == 12 and path.read_bytes() == b"line1\nline2\n"
    assert opener.requests[0].full_url.endswith("/projects/42/jobs/5")
    assert opener.requests[1].full_url.endswith("/projects/42/jobs/5/trace")
    assert opener.requests[1].get_header("Accept") is None

    opener.add(200, JOB)  # cached: only the job lookup happens
    assert fetch_trace(client, 42, 5, cache)[2] == 12
    assert len(opener.requests) == 3

    opener.add(200, JOB).add(200, b"new\n")
    assert fetch_trace(client, 42, 5, cache, refresh=True)[2] == 4

    opener.add(200, dict(JOB, status="running")).add(200, b"partial\n")
    assert fetch_trace(client, 42, 5, cache)[2] == 8


def test_header_line():
    assert header_line(JOB, 12) == "job 5 test · stage test · failed · 31s · 12 bytes · https://g/j/5"
    assert header_line({"id": 5, "name": "t", "stage": "s", "status": "running", "duration": None, "web_url": "u"}, 0) == "job 5 t · stage s · running · - · 0 bytes · u"


def test_gl_log_cli(run_gl, opener):
    # First call downloads and caches (status=failed is terminal).
    opener.add(200, PROJECT_JSON).add(200, JOB).add(200, TRACE.encode())
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--sections")
    assert code == 0, err
    assert out.splitlines()[0] == f"job 5 test · stage test · failed · 31s · {len(TRACE.encode())} bytes · https://g/j/5"
    assert "step_script · 4 lines · 27s" in out

    opener.add(200, PROJECT_JSON).add(200, JOB)  # served from cache from here on
    code, out, _ = run_gl("log", "5", "--project", "group/proj", "--section", "step_script", "--grep", "failed")
    assert code == 0 and out.splitlines()[1:] == ["3: FAILED tests/test_x.py::test_y - AssertionError", "4: ERROR: Job failed: exit code 1"]

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, _ = run_gl("log", "5", "--project", "group/proj", "--tail", "2")
    assert out.splitlines()[1:] == ["ERROR: Job failed: exit code 1", "Cleaning up project directory"]

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, _ = run_gl("log", "5", "--project", "group/proj")
    assert code == 0 and out.splitlines()[1:] == CLEAN  # fewer than 150 lines → whole log

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--tail", "0")
    assert code == 0 and "printing the whole log" in err

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--head", "0")
    assert code == 0 and "printing the whole log" in err and out.splitlines()[1:] == CLEAN

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--section", "nope")
    assert code == 1 and "no section named 'nope'" in err
