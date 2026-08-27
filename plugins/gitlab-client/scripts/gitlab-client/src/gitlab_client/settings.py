# SPDX-License-Identifier: AGPL-3.0-only
"""Settings discovery (user + project .local.md files, env, flags) and token resolution.

Precedence, highest first: CLI flags → environment → project file → user file.
The token is resolved by running `token_cmd` (a user-authored shell command line —
`shell=True` is deliberate) and lives only in this process's memory.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from .errors import ConfigError

KNOWN_KEYS = ("url", "token_cmd", "project")
USER_FILE = Path(".claude") / "gitlab-client.local.md"  # relative to $HOME
PROJECT_FILE = Path(".claude") / "gitlab-client.local.md"  # relative to the repo root
ENV_URL = "GITLAB_CLIENT_URL"
ENV_TOKEN = "GITLAB_CLIENT_TOKEN"
TOKEN_CMD_TIMEOUT = 60


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the leading `---` block: `key: value` lines and `key: |` block scalars only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if value == "|":
            block: list[str] = []
            while i < len(lines) and (not lines[i].strip() or lines[i][0].isspace()):
                block.append(lines[i])
                i += 1
            while block and not block[-1].strip():
                block.pop()
            indent = min((len(b) - len(b.lstrip()) for b in block if b.strip()), default=0)
            value = "\n".join(b[indent:] for b in block)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


Warn = Callable[[str], None]


def _stderr_warn(message: str) -> None:
    print(message, file=sys.stderr)


def read_file_settings(path: Path, warn: Warn = _stderr_warn) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = parse_frontmatter(path.read_text(encoding="utf-8"))
    for key in data:
        if key not in KNOWN_KEYS:
            warn(f"gl: warning: {path}: unknown setting '{key}' ignored")
    return {k: v for k, v in data.items() if k in KNOWN_KEYS and v}


@dataclass
class Settings:
    url: str
    token_cmd: str | None = None
    project: str | None = None
    token: str | None = None  # from GITLAB_CLIENT_TOKEN; when set, token_cmd is never run
    sources: dict[str, str] = field(default_factory=dict)


def load_settings(
    *,
    cwd: Path,
    home: Path,
    env: Mapping[str, str],
    url_flag: str | None = None,
    project_flag: str | None = None,
    warn: Warn = _stderr_warn,
) -> Settings:
    user_path = home / USER_FILE
    project_path = cwd / PROJECT_FILE
    layers = [
        ("user file", read_file_settings(user_path, warn)),
        ("project file", read_file_settings(project_path, warn)),
        ("env", {"url": env[ENV_URL]} if env.get(ENV_URL) else {}),
        ("flag", {k: v for k, v in (("url", url_flag), ("project", project_flag)) if v}),
    ]
    merged: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name, layer in layers:
        for key, value in layer.items():
            merged[key] = value
            sources[key] = name

    url = merged.get("url")
    if not url:
        raise ConfigError(
            f"no GitLab URL configured — run /gitlab-client:setup to create {user_path} (or set {ENV_URL})"
        )
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"url must start with http:// or https:// (got {url!r}, from {sources['url']})")

    token = env.get(ENV_TOKEN) or None
    token_cmd = None if token else merged.get("token_cmd")
    if not token and not token_cmd:
        raise ConfigError(
            f"no token_cmd configured in {user_path} — run /gitlab-client:setup (or set {ENV_TOKEN})"
        )
    return Settings(
        url=url.rstrip("/"),
        token_cmd=token_cmd,
        project=merged.get("project") or None,
        token=token,
        sources=sources,
    )


Runner = Callable[..., subprocess.CompletedProcess]


def resolve_token(settings: Settings, run: Runner = subprocess.run) -> str:
    """Run token_cmd and return the single line it prints. Never includes stdout in errors."""
    if settings.token:
        return settings.token
    assert settings.token_cmd, "load_settings guarantees token or token_cmd"
    try:
        proc = run(settings.token_cmd, shell=True, capture_output=True, text=True, timeout=TOKEN_CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ConfigError(f"token_cmd timed out after {TOKEN_CMD_TIMEOUT}s") from None
    stderr = (proc.stderr or "").strip()
    suffix = f"; stderr: {stderr}" if stderr else ""
    if proc.returncode != 0:
        raise ConfigError(f"token_cmd exited {proc.returncode}{suffix}")
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ConfigError(f"token_cmd must print exactly one non-empty line (got {len(lines)}){suffix}")
    return lines[0]
