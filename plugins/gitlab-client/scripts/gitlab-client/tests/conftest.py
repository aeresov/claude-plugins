# SPDX-License-Identifier: AGPL-3.0-only
"""Shared fixtures: a stub urllib opener, a Client wired to it, and a `run_gl` CLI runner."""
import io
import json
import urllib.error
from email.message import Message

import pytest

from gitlab_client.http import Client


def _bytes(body):
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode()
    return json.dumps(body).encode()


class FakeResponse:
    def __init__(self, status, body, headers, url):
        self.status = status
        self._buf = io.BytesIO(_bytes(body))
        self.headers = Message()
        for k, v in headers.items():
            self.headers[k] = v
        self.url = url

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StubOpener:
    """FIFO of canned responses; records every urllib Request it receives."""

    def __init__(self):
        self.queue = []
        self.requests = []

    def add(self, status, body=b"", headers=None):
        self.queue.append((status, body, headers or {}))
        return self

    def open(self, req, timeout=None):
        self.requests.append(req)
        assert self.queue, f"unexpected request {req.get_method()} {req.full_url}"
        status, body, headers = self.queue.pop(0)
        if status >= 400:
            msg = Message()
            for k, v in headers.items():
                msg[k] = v
            raise urllib.error.HTTPError(req.full_url, status, "error", msg, io.BytesIO(_bytes(body)))
        return FakeResponse(status, body, headers, req.full_url)

    @property
    def last(self):
        return self.requests[-1]


@pytest.fixture
def opener():
    return StubOpener()


@pytest.fixture
def client(opener):
    return Client("https://gitlab.example.com", "tok", opener=opener, sleep=lambda s: None)


@pytest.fixture
def run_gl(opener, tmp_path):
    """Run cli.main with env-provided settings, the stub client, and captured stdio.

    Returns (exit_code, stdout, stderr). Settings come from GITLAB_CLIENT_URL/TOKEN so no
    settings file or token_cmd is needed; pass `--project group/name` to skip git.
    """
    from gitlab_client.cli import main

    def _run(*argv, env=None, cwd=None, home=None):
        out, err = io.StringIO(), io.StringIO()
        base_env = {
            "GITLAB_CLIENT_URL": "https://gitlab.example.com",
            "GITLAB_CLIENT_TOKEN": "tok",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        }
        if env:
            base_env.update(env)
        code = main(
            list(argv),
            cwd=cwd or tmp_path,
            home=home or tmp_path,
            env=base_env,
            client_factory=lambda url, token: Client(url, token, opener=opener, sleep=lambda s: None),
            stdout=out,
            stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    return _run
