# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve which GitLab project a call is about: --project flag → settings → git remote.

Only the remote's *path* matters (SSH aliases and split hostnames are common); the
GET /projects/:path confirmation call is the check that the guess was right.
"""

from __future__ import annotations

import re
import subprocess
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError, HttpError
from .http import Client, encode_path_segment
from .settings import Settings

# git@host:group/name.git  or  alias:group/name  (no scheme, first ':' splits host from path)
_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?[\w.-]+:(?P<path>[^/].*)$")

Runner = Callable[..., subprocess.CompletedProcess]


def _clean(path: str) -> str:
    path = path.strip().strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path.strip("/")


def parse_remote_url(url: str, url_prefix: str = "") -> str | None:
    """git remote URL → 'group/sub/name', or None if it doesn't look like a project path."""
    url = url.strip()
    if not url:
        return None
    if "://" in url:
        path = urllib.parse.urlsplit(url).path
    else:
        m = _SCP_LIKE.match(url)
        if not m:
            return None
        path = m.group("path")
    path = _clean(path)
    prefix = url_prefix.strip("/")
    if prefix and path.startswith(prefix + "/"):
        path = path[len(prefix) + 1 :]
    return path if "/" in path else None


def parse_project_ref(ref: str, base_url: str) -> str:
    """'group/name' or a GitLab web URL (…/group/name/-/merge_requests/1) → 'group/name'."""
    ref = ref.strip()
    if "://" not in ref:
        return _clean(ref)
    base = base_url.rstrip("/")
    if ref.startswith(base + "/"):
        path = ref[len(base) :]
    else:
        path = urllib.parse.urlsplit(ref).path
    return _clean(path.split("/-/", 1)[0])


def git_output(args: list[str], cwd: Path, run: Runner = subprocess.run) -> str | None:
    try:
        proc = run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else None


def git_toplevel(cwd: Path, run: Runner = subprocess.run) -> Path | None:
    out = git_output(["rev-parse", "--show-toplevel"], cwd, run)
    return Path(out) if out else None


def git_remote_url(cwd: Path, remote: str = "origin", run: Runner = subprocess.run) -> str | None:
    return git_output(["remote", "get-url", remote], cwd, run)


@dataclass
class Project:
    id: int
    path: str
    default_branch: str
    web_url: str
    resolved_from: str


def resolve_project(client: Client, settings: Settings, *, remote: str, cwd: Path, run: Runner = subprocess.run) -> Project:
    if settings.project:
        source = "flag" if settings.sources.get("project") == "flag" else "settings"
        path = parse_project_ref(settings.project, settings.url)  # a URL works in the settings file too
    else:
        remote_url = git_remote_url(cwd, remote, run)
        if not remote_url:
            raise ConfigError(f"cannot resolve project: no --project, no project: in settings, and `git remote get-url {remote}` gave nothing")
        prefix = urllib.parse.urlsplit(settings.url).path
        parsed = parse_remote_url(remote_url, prefix)
        if not parsed:
            raise ConfigError(
                f"cannot resolve project: remote {remote} URL {remote_url!r} doesn't look like a GitLab "
                f"project path — pass --project, or set project: in .claude/gitlab-client.local.md"
            )
        path, source = parsed, f"remote:{remote}"
    try:
        data = client.request("GET", f"/projects/{encode_path_segment(path)}").json()
    except HttpError as e:
        if e.status == 404:
            raise ConfigError(
                f"project {path!r} (from {source}) not found or not visible to this token — pass --project, or set project: in .claude/gitlab-client.local.md"
            ) from None
        raise
    return Project(
        id=int(data["id"]),
        path=data["path_with_namespace"],
        default_branch=data.get("default_branch") or "",
        web_url=data.get("web_url") or "",
        resolved_from=source,
    )
