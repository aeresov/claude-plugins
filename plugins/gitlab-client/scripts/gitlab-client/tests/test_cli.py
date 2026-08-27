# SPDX-License-Identifier: AGPL-3.0-only
import json

import pytest

PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "https://gitlab.example.com/group/proj"}


def test_no_command_is_usage(run_gl):
    code, out, err = run_gl()
    assert code == 2 and "usage:" in err


def test_api_get_with_project_placeholder_and_fields(run_gl, opener):
    opener.add(200, PROJECT_JSON).add(200, [{"iid": 1, "title": "A", "author": {"username": "u"}, "big": "x" * 100}])
    code, out, err = run_gl("api", "GET", "/projects/:project/merge_requests", "state=opened", "--project", "group/proj", "--fields", "iid,title,author.username")
    assert code == 0, err
    assert json.loads(out) == [{"iid": 1, "title": "A", "author.username": "u"}]
    assert opener.requests[0].full_url == "https://gitlab.example.com/api/v4/projects/group%2Fproj"
    assert opener.requests[1].full_url == "https://gitlab.example.com/api/v4/projects/group%2Fproj/merge_requests?state=opened"


def test_api_params_after_options(run_gl, opener):
    opener.add(200, [])
    code, out, err = run_gl("api", "GET", "/projects", "--fields", "id", "search=x", "archived=false")
    assert code == 0, err
    assert opener.last.full_url.endswith("/projects?search=x&archived=false")


def test_api_post_builds_json_body(run_gl, opener):
    opener.add(201, {"iid": 9})
    code, out, _ = run_gl("api", "POST", "/projects/1/merge_requests", "title=Draft: x", "reviewer_ids:=[3]", "squash:=true")
    assert code == 0 and json.loads(out)["iid"] == 9
    assert json.loads(opener.last.data) == {"title": "Draft: x", "reviewer_ids": [3], "squash": True}
    assert opener.last.full_url.endswith("/projects/1/merge_requests")  # params go to the body, not the query


def test_api_raw_json_body(run_gl, opener):
    opener.add(201, {"id": 1})
    code, *_ = run_gl("api", "POST", "/projects/1/pipeline", "--json", '{"ref": "main", "variables": [{"key": "A", "value": "1"}]}')
    assert code == 0 and json.loads(opener.last.data)["variables"][0]["key"] == "A"
    code, _, err = run_gl("api", "POST", "/projects/1/pipeline", "--json", "{oops")
    assert code == 2 and "bad JSON" in err


def test_api_refuses_policy_before_network(run_gl, opener):
    # A :project path would normally trigger token_cmd + GET /projects/:path first — the refusal must come before that.
    code, out, err = run_gl("api", "PUT", "/projects/:project/merge_requests/2/merge", "--project", "group/proj")
    assert code == 3 and "write policy" in err and opener.requests == []


def test_api_refuses_sudo_parameter(run_gl, opener):
    code, _, err = run_gl("api", "GET", "/user", "sudo=someone-else")
    assert code == 3 and "sudo" in err and opener.requests == []
    code, _, err = run_gl("api", "POST", "/projects/1/merge_requests/2/notes", "--json", '{"body": "x", "sudo": 7}')
    assert code == 3 and "sudo" in err and opener.requests == []
    code, _, err = run_gl("api", "GET", "/user?sudo=alice")  # query string embedded in PATH
    assert code == 3 and "sudo" in err and opener.requests == []
    code, _, err = run_gl("api", "POST", "/projects/1/jobs/5/retry?x=1&sudo[]=alice")
    assert code == 3 and "sudo" in err and opener.requests == []


def test_api_refuses_hash_in_path_before_network(run_gl, opener):
    code, _, err = run_gl("api", "PUT", "/projects/1#/merge_requests/5", "title=x")
    assert code == 3 and "write policy" in err and opener.requests == []


def test_api_delete_is_usage_error(run_gl, capsys):
    with pytest.raises(SystemExit) as exc:  # argparse rejects the verb before any command runs
        run_gl("api", "DELETE", "/projects/1")
    assert exc.value.code == 2
    assert "invalid choice: 'DELETE'" in capsys.readouterr().err


def test_api_all_and_out(run_gl, opener, tmp_path):
    opener.add(200, [1], {"X-Next-Page": "2"}).add(200, [2], {"X-Next-Page": ""})
    code, out, _ = run_gl("api", "GET", "/projects", "--all")
    assert code == 0 and json.loads(out) == [1, 2]

    code, _, err = run_gl("api", "POST", "/projects/1/pipeline", "ref=main", "--all")
    assert code == 2 and "--all only applies to GET" in err

    opener.add(200, b"raw bytes")
    dest = tmp_path / "f.bin"
    code, out, _ = run_gl("api", "GET", "/projects/1/repository/files/a%2Fb/raw", "ref=main", "--out", str(dest))
    assert code == 0 and out == f"wrote 9 bytes to {dest}\n" and dest.read_bytes() == b"raw bytes"
    assert opener.last.full_url.endswith("/raw?ref=main")


def test_api_non_json_response_printed_verbatim(run_gl, opener):
    opener.add(200, b"plain text file\n")
    code, out, _ = run_gl("api", "GET", "/projects/1/repository/files/README.md/raw", "ref=main")
    assert code == 0 and out == "plain text file\n"
    assert opener.last.get_header("Accept") is None  # raw routes don't ask for JSON
    opener.add(200, b'{"a":1,"b":[1,2]}')  # a .json file: printed byte-for-byte, not re-serialised
    code, out, _ = run_gl("api", "GET", "/projects/1/repository/files/cfg.json/raw", "ref=main", "--fields", "a")
    assert code == 0 and out == '{"a":1,"b":[1,2]}'
    opener.add(200, {"id": 1})
    run_gl("api", "GET", "/projects/1")
    assert opener.last.get_header("Accept") == "application/json"


def test_http_error_exit_1(run_gl, opener):
    opener.add(404, {"message": "404 Not Found"})
    code, out, err = run_gl("api", "GET", "/projects/1")
    assert code == 1 and err.startswith("gl: HTTP 404 GET")


def test_config_error_exit_2(run_gl):
    code, out, err = run_gl("api", "GET", "/user", env={"GITLAB_CLIENT_URL": ""})
    assert code == 2 and "no GitLab URL configured" in err


def test_project_command(run_gl, opener):
    opener.add(200, PROJECT_JSON)
    code, out, _ = run_gl("project", "--project", "group/proj")
    assert code == 0
    assert json.loads(out) == {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "https://gitlab.example.com/group/proj", "resolved_from": "flag"}


def test_version_warns_on_non_15(run_gl, opener):
    opener.add(200, {"version": "15.11.13-ee", "revision": "abc", "enterprise": True})
    code, out, err = run_gl("version")
    assert code == 0 and err == ""
    assert json.loads(out) == {"version": "15.11.13-ee", "revision": "abc", "enterprise": True, "url": "https://gitlab.example.com"}
    assert opener.last.full_url.endswith("/api/v4/metadata")

    opener.add(200, {"version": "16.4.0", "revision": "abc", "enterprise": True})
    code, out, err = run_gl("version")
    assert code == 0 and "written for GitLab 15.x; this instance is 16.4.0" in err

    opener.add(200, {"version": "15.2.5", "revision": "abc"})  # CE, pre-15.6: no `enterprise` field at all
    code, out, err = run_gl("version")
    assert code == 0 and "GitLab 15.2.5 predates some 15.x endpoints" in err and json.loads(out)["enterprise"] is None

    opener.add(200, {"version": "16.4.0", "revision": "abc", "enterprise": True})
    code, out, err = run_gl("version", "--quiet")
    assert code == 0 and err == ""


def test_api_rejects_conflicting_flags(run_gl, tmp_path):
    code, _, err = run_gl("api", "GET", "/projects", "--json", '{"a": 1}')
    assert code == 2 and "--json only applies to POST/PUT" in err
    for extra in (("--all",), ("--fields", "id")):
        code, _, err = run_gl("api", "GET", "/projects", "--out", str(tmp_path / "f"), *extra)
        assert code == 2 and "cannot be combined with --all or --fields" in err


def test_api_out_does_not_clobber_the_destination_on_failure(run_gl, opener, tmp_path):
    dest = tmp_path / "keep.json"
    dest.write_bytes(b"previous contents")
    opener.add(500, b"boom")
    code, _, err = run_gl("api", "GET", "/projects/1/jobs/5/trace", "--out", str(dest))
    assert code == 1 and "HTTP 500" in err
    assert dest.read_bytes() == b"previous contents"  # untouched, not truncated
    assert not (tmp_path / "keep.json.part").exists()


def test_api_out_creates_missing_parent_directories(run_gl, opener, tmp_path):
    opener.add(200, b"raw bytes")
    dest = tmp_path / "nested" / "dir" / "f.bin"
    code, _, _ = run_gl("api", "GET", "/projects/1/jobs/5/trace", "--out", str(dest))
    assert code == 0 and dest.read_bytes() == b"raw bytes"
