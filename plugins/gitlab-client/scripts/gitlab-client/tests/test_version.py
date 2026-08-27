# SPDX-License-Identifier: AGPL-3.0-only
import re
from pathlib import Path

import gitlab_client
from gitlab_client.errors import ConfigError, GlError, HttpError, PolicyError

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version_matches_pyproject():
    toml_version = re.search(r'^version *= *"([^"]+)"', PYPROJECT.read_text(), re.M).group(1)
    assert gitlab_client.__version__ == toml_version


def test_exit_codes():
    assert GlError("x").exit_code == 1
    assert ConfigError("x").exit_code == 2
    assert PolicyError("x").exit_code == 3
    err = HttpError(404, "GET", "https://h/api/v4/x", "404 Project Not Found")
    assert err.exit_code == 1
    assert (err.status, err.method, err.url, err.detail) == (404, "GET", "https://h/api/v4/x", "404 Project Not Found")
    assert str(err) == "HTTP 404 GET https://h/api/v4/x: 404 Project Not Found"
