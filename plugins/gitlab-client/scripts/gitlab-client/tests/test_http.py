# SPDX-License-Identifier: AGPL-3.0-only
import urllib.error
import urllib.request

import pytest

from gitlab_client import __version__
from gitlab_client.errors import GlError, HttpError
from gitlab_client.http import AuthStrippingRedirectHandler, Client, describe_error


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
    with pytest.raises(HttpError, match="403 Forbidden.*role too low"):
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


def test_describe_error_plain_text():
    assert describe_error(502, b"<html>Bad Gateway</html>") == "<html>Bad Gateway</html>"
    assert describe_error(500, b"") == "(empty body)"
