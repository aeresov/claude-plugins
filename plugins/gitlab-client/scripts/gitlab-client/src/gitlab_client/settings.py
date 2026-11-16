# SPDX-License-Identifier: AGPL-3.0-only
"""Settings discovery (user + project .local.md files, env, flags) and token resolution.

Precedence, highest first: CLI flags → environment → project file → user file. The project
file may only set `project`; `url` and `token_cmd` come from the user file or the environment.
The token is resolved by running `token_cmd` (a user-authored shell command line —
`shell=True` is deliberate) and lives only in this process's memory. `run_token_cmd` starts it
the way a secret-store CLI can be trusted to behave: from `/`, stdin `/dev/null`, a session of
its own, its whole process group killed on timeout; `resolve_token` lets one run at a time.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import ConfigError

KNOWN_KEYS = ("url", "token_cmd", "project")
USER_FILE = Path(".claude") / "gitlab-client.local.md"  # relative to $HOME
PROJECT_FILE = Path(".claude") / "gitlab-client.local.md"  # relative to the repo root
# A checkout's own file must not choose where the user's token goes or what runs to fetch it:
# `url` alone would send the user-level token to that host, and `token_cmd` would run inside gl,
# out of sight of Claude Code's per-command permission prompt.
PROJECT_FILE_IGNORED = ("url", "token_cmd")
ENV_URL = "GITLAB_CLIENT_URL"
ENV_TOKEN = "GITLAB_CLIENT_TOKEN"
TOKEN_CMD_TIMEOUT = 60
# Every holder is itself bounded by TOKEN_CMD_TIMEOUT, so a waiter normally gets the lock in time.
TOKEN_LOCK_WAIT = 60
_LOCK_POLL = 0.1


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
    project_file = read_file_settings(project_path, warn)
    for key in PROJECT_FILE_IGNORED:
        if project_file.pop(key, None) is not None:
            warn(f"gl: warning: {project_path}: '{key}' ignored — a project file may only set 'project'")
    layers = [
        ("user file", read_file_settings(user_path, warn)),
        ("project file", project_file),
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
        raise ConfigError(f"no GitLab URL configured — run gitlab-client-setup to create {user_path} (or set {ENV_URL})")
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"url must start with http:// or https:// (got {url!r}, from {sources['url']})")

    token = env.get(ENV_TOKEN) or None
    token_cmd = None if token else merged.get("token_cmd")
    if not token and not token_cmd:
        raise ConfigError(f"no token_cmd configured in {user_path} — run gitlab-client-setup (or set {ENV_TOKEN})")
    return Settings(
        url=url.rstrip("/"),
        token_cmd=token_cmd,
        project=merged.get("project") or None,
        token=token,
        sources=sources,
    )


Runner = Callable[[str, float], Optional[subprocess.CompletedProcess]]


def run_token_cmd(cmd: str, timeout: float) -> subprocess.CompletedProcess | None:
    """Run `cmd` through the shell; None if it didn't finish within `timeout` seconds.

    From `/`: gl runs inside a checkout, and some secret-store CLIs read their current directory
    first (Keeper Commander runs a file named like its command as a command script, and takes a
    `config.json` as its config). stdin `/dev/null` and a session of its own: nobody can answer a
    prompt inside gl, and without a controlling terminal a prompt that opens /dev/tty (getpass)
    fails at once. On a timeout or anything else that stops us waiting (Ctrl-C), the whole process
    group is killed, so nothing it started outlives gl.
    """
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True  # its .output may hold the token: let it go here, unreferenced
    finally:
        if proc.returncode is None:
            _kill_group(proc)
    if timed_out:
        return None
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _kill_group(proc: subprocess.Popen) -> None:
    # Only while the leader is unreaped does its pid still name the group start_new_session made.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


@contextmanager
def _token_lock(path: Path | None) -> Iterator[None]:
    """Hold an exclusive lock on `path` while token_cmd runs, waiting up to TOKEN_LOCK_WAIT.

    Each Keeper Commander sign-in rotates its saved session, so two at once can cost one of them
    that session. A lock that can't be opened (no path, a sandbox that blocks the cache directory)
    or a file system without flock leaves the run unlocked rather than failing it.
    """
    fd = _open_lock(path)
    if fd is None:
        yield
        return
    try:
        deadline = time.monotonic() + TOKEN_LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConfigError(f"another gl call has been running token_cmd for {TOKEN_LOCK_WAIT}s; gave up waiting for it") from None
                time.sleep(_LOCK_POLL)
            except OSError:
                break
        yield
    finally:
        os.close(fd)


def _open_lock(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never passed to token_cmd (non-inheritable, and Popen closes other fds): one that outlived gl
        # must not keep holding the lock.
        return os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None


def resolve_token(settings: Settings, *, lock_path: Path | None = None, run: Runner = run_token_cmd) -> str:
    """Run token_cmd and return the single line it prints. Never includes stdout in errors."""
    if settings.token:
        return settings.token
    assert settings.token_cmd, "load_settings guarantees token or token_cmd"
    with _token_lock(lock_path):
        try:
            proc = run(settings.token_cmd, TOKEN_CMD_TIMEOUT)
        except OSError as e:
            raise ConfigError(f"could not start token_cmd: {e.strerror or e}") from None
    if proc is None:
        raise ConfigError(f"token_cmd timed out after {TOKEN_CMD_TIMEOUT}s")
    stderr = (proc.stderr or "").strip()
    suffix = f"; stderr: {stderr}" if stderr else ""
    if proc.returncode != 0:
        raise ConfigError(f"token_cmd exited {proc.returncode}{suffix}")
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ConfigError(f"token_cmd must print exactly one non-empty line (got {len(lines)}){suffix}")
    return lines[0]
