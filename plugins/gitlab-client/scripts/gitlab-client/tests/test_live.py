# SPDX-License-Identifier: AGPL-3.0-only
"""Opt-in smoke test against a real instance. Skipped unless GITLAB_CLIENT_LIVE=1."""
import os

import pytest

from gitlab_client.http import Client

pytestmark = pytest.mark.skipif(os.environ.get("GITLAB_CLIENT_LIVE") != "1", reason="set GITLAB_CLIENT_LIVE=1 to run")


@pytest.fixture
def live():
    url, token = os.environ.get("GITLAB_CLIENT_URL"), os.environ.get("GITLAB_CLIENT_TOKEN")
    if not url or not token:
        pytest.skip("GITLAB_CLIENT_URL and GITLAB_CLIENT_TOKEN are required")
    return Client(url, token)


def test_metadata_and_user(live):
    meta = live.request("GET", "/metadata").json()
    assert meta["version"].split(".")[0].isdigit()
    me = live.request("GET", "/user").json()
    assert me["username"]
