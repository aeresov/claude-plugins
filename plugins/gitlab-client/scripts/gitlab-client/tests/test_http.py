# SPDX-License-Identifier: AGPL-3.0-only
import urllib.error
import urllib.request

import pytest

from gitlab_client import __version__
from gitlab_client.errors import ConfigError, GlError, HttpError, PolicyError
from gitlab_client.http import (
    AuthStrippingRedirectHandler,
    Client,
    check_write_policy,
    describe_error,
    encode_path_segment,
    parse_params,
    project_fields,
    substitute_project,
)


def test_request_sets_headers_and_builds_url(client, opener):
    opener.add(200, {"ok": True}, {"X-Next-Page": "2"})
    resp = client.request("GET", "/projects", query={"search": "a b", "archived": False, "topic": ["x", "y"], "skip": None})
    req = opener.last
    assert req.full_url == "https://gitlab.example.com/api/v4/projects?search=a+b&archived=false&topic%5B%5D=x&topic%5B%5D=y"
    assert req.get_method() == "GET"
    assert req.get_header("Private-token") == "tok"
    assert req.get_header("Accept") == "application/json"
    assert req.get_header("User-agent") == f"gitlab-client/{__version__} (claude-code plugin)"
    assert resp.status == 200 and resp.json() == {"ok": True}
    assert resp.header("x-next-page") == "2" and resp.header("missing") == ""


def test_path_without_leading_slash_and_absolute_url(client, opener):
    opener.add(200, []).add(200, [])
    client.request("GET", "user")
    assert opener.last.full_url == "https://gitlab.example.com/api/v4/user"
    client.request("GET", "ignored", absolute_url="https://gitlab.example.com/api/v4/x?page_token=abc")
    assert opener.last.full_url == "https://gitlab.example.com/api/v4/x?page_token=abc"


def test_post_sends_json_body(client, opener):
    opener.add(201, {"iid": 7})
    resp = client.request("POST", "/projects/1/merge_requests", json_body={"title": "T", "reviewer_ids": [3]})
    req = opener.last
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert req.data == b'{"title": "T", "reviewer_ids": [3]}'
    assert resp.status == 201


def test_stream_to_writes_chunks(client, opener, tmp_path):
    opener.add(200, b"x" * 3_000_000)
    dest = tmp_path / "blob"
    with open(dest, "wb") as fh:
        resp = client.request("GET", "/projects/1/jobs/2/trace", accept_json=False, stream_to=fh)
    assert dest.stat().st_size == 3_000_000 and resp.bytes_written == 3_000_000 and resp.body == b""
    assert opener.last.get_header("Accept") is None


def test_http_error_maps_message_shapes(client, opener):
    opener.add(404, {"message": "404 Project Not Found"})
    with pytest.raises(HttpError) as e:
        client.request("GET", "/projects/nope")
    assert e.value.status == 404
    assert "404 Project Not Found" in str(e.value) and "not-visible" in str(e.value)

    opener.add(404, {"error": "404 Not Found"})
    with pytest.raises(HttpError, match="404 Not Found"):
        client.request("GET", "/nonsense")

    opener.add(400, {"message": {"title": ["can't be blank"]}})
    with pytest.raises(HttpError, match=r'\{"title": \["can\'t be blank"\]\}'):
        client.request("POST", "/projects/1/merge_requests", json_body={})

    opener.add(403, {"error": "insufficient_scope", "error_description": "The request requires higher privileges than provided by the access token.", "scope": "api"})
    with pytest.raises(HttpError, match="token lacks the 'api' scope"):
        client.request("POST", "/projects/1/merge_requests", json_body={"title": "x"})

    opener.add(403, {"message": "403 Forbidden"})
    with pytest.raises(HttpError, match="403 Forbidden.*role below Developer"):
        client.request("POST", "/projects/1/jobs/2/retry")

    opener.add(401, {"message": "401 Unauthorized"})
    with pytest.raises(HttpError, match="token rejected"):
        client.request("GET", "/user")


def test_429_retries_once_with_retry_after(opener):
    sleeps = []
    client = Client("https://gitlab.example.com", "tok", opener=opener, sleep=sleeps.append)
    opener.add(429, b"Retry later", {"Retry-After": "7"}).add(200, [])
    assert client.request("GET", "/projects").json() == []
    assert sleeps == [7.0] and len(opener.requests) == 2

    opener.add(429, b"Retry later", {"Retry-After": "900"}).add(429, b"Retry later")
    with pytest.raises(HttpError, match="Retry later.*rate limited"):
        client.request("GET", "/projects")
    assert sleeps == [7.0, 30.0]


def test_network_error_is_glerror(client):
    class Boom:
        def open(self, req, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    client._opener = Boom()
    with pytest.raises(GlError, match="gitlab.example.com.*ConnectionRefusedError"):
        client.request("GET", "/user")


def test_redirect_handler_strips_auth_cross_host_only():
    handler = AuthStrippingRedirectHandler("gitlab.example.com")
    req = urllib.request.Request(
        "https://gitlab.example.com/api/v4/x",
        headers={"PRIVATE-TOKEN": "tok", "Authorization": "Bearer tok", "Accept": "*/*"},
    )
    same = handler.redirect_request(req, None, 302, "Found", {}, "https://gitlab.example.com/api/v4/y")
    assert same.get_header("Private-token") == "tok"
    cross = handler.redirect_request(req, None, 302, "Found", {}, "https://s3.storage.example/bucket/obj?X-Amz-Signature=abc")
    assert cross.get_header("Private-token") is None and cross.get_header("Authorization") is None
    assert cross.get_header("Accept") == "*/*"
    downgrade = handler.redirect_request(req, None, 302, "Found", {}, "http://gitlab.example.com/api/v4/y")
    assert downgrade.get_header("Private-token") is None  # same host, but https→http


def test_client_wires_scheme_into_redirect_handler():
    client = Client("http://gitlab.internal:8080", "tok")
    assert (client.host, client.scheme) == ("gitlab.internal:8080", "http")
    handler = next(h for h in client._opener.handlers if isinstance(h, AuthStrippingRedirectHandler))
    assert (handler.api_host, handler.scheme) == ("gitlab.internal:8080", "http")


def test_describe_error_plain_text():
    assert describe_error(502, b"<html>Bad Gateway</html>") == "<html>Bad Gateway</html>"
    assert describe_error(500, b"") == "(empty body)"


def test_parse_params():
    items = ["state=opened", "per_page=5", "labels[]=a", "labels[]=b", "squash:=true",
             'vars:=[{"key":"K","value":"v"}]', "range[start]=1", "empty=", "title=a=b"]
    assert parse_params(items) == {
        "state": "opened", "per_page": "5", "labels": ["a", "b"], "squash": True,
        "vars": [{"key": "K", "value": "v"}], "range[start]": "1", "empty": "", "title": "a=b",
    }
    with pytest.raises(ConfigError, match="bad JSON for x"):
        parse_params(["x:={oops"])
    with pytest.raises(ConfigError, match="key=value"):
        parse_params(["novalue"])


def test_encode_and_substitute_project():
    assert encode_path_segment("grp/sub/proj") == "grp%2Fsub%2Fproj"
    assert encode_path_segment("feature/x.y") == "feature%2Fx.y"
    assert substitute_project("/projects/:project/merge_requests", "grp/sub/proj") == "/projects/grp%2Fsub%2Fproj/merge_requests"
    assert substitute_project("/user", None) == "/user"
    with pytest.raises(ConfigError, match=":project"):
        substitute_project("/projects/:project", None)


ALLOWED = [
    ("POST", "/projects/42/merge_requests"),
    ("POST", "/projects/grp%2Fproj/merge_requests"),
    ("PUT", "/projects/42/merge_requests/7"),
    ("POST", "/projects/42/merge_requests/7/notes"),
    ("POST", "/projects/42/merge_requests/7/discussions"),
    ("POST", "/projects/42/merge_requests/7/discussions/6a1f0c2e9b3d4f5a6b7c8d9e0f1a2b3c4d5e6f70/notes"),
    ("PUT", "/projects/42/merge_requests/7/discussions/6a1f0c2e9b3d4f5a6b7c8d9e0f1a2b3c4d5e6f70?resolved=true"),
    ("POST", "/projects/42/pipeline"),
    ("POST", "/projects/42/pipelines/99/retry"),
    ("POST", "/projects/42/pipelines/99/cancel"),
    ("POST", "/projects/42/jobs/5/retry"),
    ("POST", "/projects/42/jobs/5/cancel"),
    ("POST", "/projects/42/jobs/5/play"),
    ("POST", "/projects/42/jobs/5/artifacts/keep"),
    ("post", "/projects/42/jobs/5/play/"),
]
REFUSED = [
    ("PUT", "/projects/42/merge_requests/7/merge"),
    ("POST", "/projects/42/merge_requests/7/approve"),
    ("POST", "/projects/42/merge_requests/7/unapprove"),
    ("POST", "/projects/42/merge_requests/7/approvals"),
    ("PUT", "/projects/42/merge_requests/7/rebase"),
    ("POST", "/projects/42/jobs/5/erase"),
    ("POST", "/projects/42/repository/branches"),
    ("POST", "/projects/42/repository/files/a.txt"),
    ("PUT", "/projects/42"),
    ("POST", "/projects/42/members"),
    ("POST", "/projects/42/variables"),
    ("POST", "/projects/42/hooks"),
    ("POST", "/projects/42/protected_branches"),
    ("POST", "/users"),
    ("POST", "/personal_access_tokens"),
    ("POST", "/admin/ci/variables"),
    ("POST", "/projects/42/merge_requests/7/notes/1"),
    ("PUT", "/projects/42/merge_requests/7/notes/1"),
    ("POST", "/projects/42/pipelines"),
    ("PUT", "/projects/42/pipelines/99/metadata"),
]


@pytest.mark.parametrize("method,path", ALLOWED)
def test_write_policy_allows(method, path):
    check_write_policy(method, path)


@pytest.mark.parametrize("method,path", REFUSED)
def test_write_policy_refuses(method, path):
    with pytest.raises(PolicyError, match="write policy"):
        check_write_policy(method, path)


@pytest.mark.parametrize(
    "path",
    [
        "/projects/1#/merge_requests/5",  # urllib truncates at '#': would send PUT /projects/1
        "/projects/1/merge_requests/5 ",  # trailing space
        "/projects/1/merge_requests/5\n",  # control character
        "/projects/1/merge_requests/５",  # fullwidth digit: \d matches it, http.client would reject it
        "/projects/../merge_requests",
        "/projects/%2e%2e/merge_requests",
    ],
)
def test_write_policy_refuses_paths_that_do_not_round_trip(path):
    with pytest.raises(PolicyError, match="write policy"):
        check_write_policy("PUT", path)
    with pytest.raises(PolicyError, match="write policy"):
        check_write_policy("GET", path)  # the canonical-path rule applies to every verb


def test_write_policy_other_verbs():
    check_write_policy("get", "/anything/at/all")
    with pytest.raises(PolicyError, match="DELETE"):
        check_write_policy("DELETE", "/projects/1/pipelines/2")
    with pytest.raises(PolicyError, match="PATCH"):
        check_write_policy("PATCH", "/projects/1")


def test_paginate_offset(client, opener):
    opener.add(200, [1, 2], {"X-Next-Page": "2"}).add(200, [3], {"X-Next-Page": ""})
    warnings = []
    assert client.paginate("/projects/1/merge_requests", {"state": "opened"}, warn=warnings.append) == [1, 2, 3]
    urls = [r.full_url for r in opener.requests]
    assert urls[0].endswith("/merge_requests?state=opened&per_page=100&page=1")
    assert urls[1].endswith("/merge_requests?state=opened&per_page=100&page=2")
    assert warnings == []


def test_paginate_stops_on_empty_page_without_header(client, opener):
    opener.add(200, [1], {}).add(200, [], {})
    assert client.paginate("/x", None) == [1]
    assert len(opener.requests) == 1  # no x-next-page and no Link → done after the first page


def test_paginate_offset_falls_back_to_link_header(client, opener):
    nxt = "https://gitlab.example.com/api/v4/x?per_page=100&page=2"
    opener.add(200, [1], {"Link": f'<{nxt}>; rel="next"'}).add(200, [2], {})
    assert client.paginate("/x", None) == [1, 2]
    assert opener.requests[1].full_url == nxt


def test_paginate_keyset_for_tree(client, opener):
    nxt = "https://gitlab.example.com/api/v4/projects/1/repository/tree?pagination=keyset&per_page=100&page_token=abc"
    opener.add(200, [{"name": "a"}], {"Link": f'<{nxt}>; rel="next"'}).add(200, [{"name": "b"}], {})
    assert client.paginate("/projects/1/repository/tree", {"recursive": True}) == [{"name": "a"}, {"name": "b"}]
    first = opener.requests[0].full_url
    assert "pagination=keyset" in first and "recursive=true" in first
    assert "&page=" not in first and "?page=" not in first
    assert opener.requests[1].full_url == nxt


def test_paginate_reanchors_link_urls_on_our_origin(client, opener):
    opener.add(200, [1], {"Link": '<http://gitlab.example.com/api/v4/x?page=2>; rel="next"'}).add(200, [2], {"Link": '<https://evil.example/steal?page=3>; rel="next"'}).add(200, [], {})
    assert client.paginate("/x", None) == [1, 2]
    assert opener.requests[1].full_url == "https://gitlab.example.com/api/v4/x?page=2"  # scheme restored
    assert opener.requests[2].full_url == "https://gitlab.example.com/steal?page=3"  # host restored
    assert all(r.get_header("Private-token") == "tok" for r in opener.requests)


def test_paginate_max_cap_warns(client, opener):
    opener.add(200, [1, 2, 3], {"X-Next-Page": "2"})
    warnings = []
    assert client.paginate("/x", None, max_items=2, warn=warnings.append) == [1, 2]
    assert len(warnings) == 1 and "--max 2" in warnings[0]


def test_paginate_requires_array(client, opener):
    opener.add(200, {"not": "a list"})
    with pytest.raises(GlError, match="expects an array"):
        client.paginate("/x", None)


def test_project_fields():
    data = [{"id": 1, "author": {"username": "u"}, "x": 0}, {"id": 2}]
    assert project_fields(data, ["id", "author.username"]) == [
        {"id": 1, "author.username": "u"},
        {"id": 2, "author.username": None},
    ]
    assert project_fields({"a": {"b": 1}}, ["a.b"]) == {"a.b": 1}
    assert project_fields({"notes": [{"body": "b"}, {"body": "c"}]}, ["notes.0.body", "notes.5.body", "notes.x"]) == {"notes.0.body": "b", "notes.5.body": None, "notes.x": None}
    assert project_fields("scalar", ["a"]) == "scalar"
