# SPDX-License-Identifier: AGPL-3.0-only
import subprocess

import pytest

from gitlab_client.errors import ConfigError, HttpError
from gitlab_client.project import (
    Project,
    git_remote_url,
    git_toplevel,
    parse_project_ref,
    parse_remote_url,
    resolve_project,
)
from gitlab_client.settings import Settings


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@gitlab.example.com:group/proj.git", "group/proj"),
        ("git@gitlab.example.com:group/sub/proj", "group/sub/proj"),
        ("ssh://git@gitlab.example.com:2222/group/proj.git", "group/proj"),
        ("https://gitlab.example.com/group/proj.git", "group/proj"),
        ("https://user:pw@gitlab.example.com/group/proj", "group/proj"),
        ("https://gitlab.example.com/group/proj/", "group/proj"),
        ("gl-alias:group/proj.git", "group/proj"),
        ("https://gitlab.example.com/proj.git", None),
        ("/srv/git/repo.git", None),
        ("", None),
    ],
)
def test_parse_remote_url(url, expected):
    assert parse_remote_url(url) == expected


def test_parse_remote_url_strips_relative_url_prefix():
    assert parse_remote_url("https://host.example/gitlab/group/proj.git", "/gitlab") == "group/proj"
    assert parse_remote_url("https://host.example/gitlab/group/proj.git", "") == "gitlab/group/proj"
    assert parse_remote_url("git@host.example:group/proj.git", "/gitlab") == "group/proj"


def test_parse_project_ref():
    base = "https://gitlab.example.com"
    assert parse_project_ref("group/proj", base) == "group/proj"
    assert parse_project_ref(" group/proj/ ", base) == "group/proj"
    assert parse_project_ref("https://gitlab.example.com/group/sub/proj/-/merge_requests/12", base) == "group/sub/proj"
    assert parse_project_ref("https://gitlab.example.com/group/proj.git", base) == "group/proj"
    assert parse_project_ref("https://host/gitlab/group/proj/-/pipelines", "https://host/gitlab") == "group/proj"
    assert parse_project_ref("https://other.host/group/proj", base) == "group/proj"


def git_run(remote_url, toplevel="/repo"):
    def run(cmd, **kwargs):
        if cmd[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(cmd, 0, remote_url + "\n", "")
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, toplevel + "\n", "")
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: not a git repository")

    return run


def test_git_helpers(tmp_path):
    assert git_remote_url(tmp_path, "origin", run=git_run("git@h:g/p.git")) == "git@h:g/p.git"
    assert git_remote_url(tmp_path, "origin", run=git_run("")) is None
    assert str(git_toplevel(tmp_path, run=git_run("x", "/work/repo"))) == "/work/repo"

    def broken(cmd, **kwargs):
        raise FileNotFoundError("git")

    assert git_remote_url(tmp_path, run=broken) is None and git_toplevel(tmp_path, run=broken) is None


PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "https://gitlab.example.com/group/proj"}


def base_settings(**kw):
    return Settings(url="https://gitlab.example.com", token="t", **kw)


def test_resolve_from_remote(client, opener, tmp_path):
    opener.add(200, PROJECT_JSON)
    p = resolve_project(client, base_settings(), remote="origin", cwd=tmp_path, run=git_run("git@gitlab.example.com:group/proj.git"))
    assert p == Project(42, "group/proj", "main", "https://gitlab.example.com/group/proj", "remote:origin")
    assert opener.last.full_url == "https://gitlab.example.com/api/v4/projects/group%2Fproj"


def test_resolve_from_remote_with_relative_url_install(client, opener, tmp_path):
    opener.add(200, PROJECT_JSON)
    s = Settings(url="https://host.example/gitlab", token="t")
    p = resolve_project(client, s, remote="upstream", cwd=tmp_path, run=git_run("https://host.example/gitlab/group/proj.git"))
    assert p.resolved_from == "remote:upstream"
    assert opener.last.full_url.endswith("/projects/group%2Fproj")


def test_resolve_from_flag_url(client, opener, tmp_path):
    opener.add(200, PROJECT_JSON)
    s = base_settings(project="https://gitlab.example.com/group/proj/-/merge_requests/3", sources={"project": "flag"})
    p = resolve_project(client, s, remote="origin", cwd=tmp_path, run=git_run("ignored"))
    assert p.resolved_from == "flag" and opener.last.full_url.endswith("/projects/group%2Fproj")


def test_resolve_from_settings_file(client, opener, tmp_path):
    opener.add(200, PROJECT_JSON)
    s = base_settings(project="group/proj", sources={"project": "project file"})
    assert resolve_project(client, s, remote="origin", cwd=tmp_path, run=git_run("ignored")).resolved_from == "settings"


def test_resolve_errors(client, opener, tmp_path):
    s = base_settings()
    with pytest.raises(ConfigError, match="git remote get-url origin"):
        resolve_project(client, s, remote="origin", cwd=tmp_path, run=git_run(""))
    with pytest.raises(ConfigError, match="doesn't look like a GitLab project path"):
        resolve_project(client, s, remote="origin", cwd=tmp_path, run=git_run("/srv/git/repo.git"))
    opener.add(404, {"message": "404 Project Not Found"})
    with pytest.raises(ConfigError, match="'group/proj' \\(from remote:origin\\) not found or not visible.*--project"):
        resolve_project(client, s, remote="origin", cwd=tmp_path, run=git_run("git@h:group/proj.git"))
    opener.add(500, b"boom")
    with pytest.raises(HttpError):
        resolve_project(client, s, remote="origin", cwd=tmp_path, run=git_run("git@h:group/proj.git"))
