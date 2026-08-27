# gitlab-client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `plugins/gitlab-client/` — a Claude Code plugin that drives an on-prem GitLab 15.11 through a bundled pure-stdlib CLI (`gl`) for repo browsing, merge requests, and CI/CD, with a skill, a read-only triage subagent, and `/setup` + `/doctor`.

**Architecture:** A Python package `gitlab_client` (argparse CLI, `urllib` transport, no third-party deps) launched by `scripts/gl`; the client reads the two settings files and runs `token_cmd` itself so the PAT never touches a command line. A generic `gl api` verb plus four helpers (`project`, `log`, `diff`, `artifacts`) where raw JSON is unsafe for context. Writes are an allow-list enforced in code. Skill + six reference docs teach the 15.11 endpoint cookbook; the `pipeline-debugger` subagent does failed-pipeline triage in isolation.

**Tech Stack:** Python ≥ 3.10 stdlib only (`argparse`, `urllib`, `json`, `subprocess`, `zipfile`, `re`), `uv` + `pytest ≥ 9` for tests, `uv_build` backend, bash launcher. Claude Code plugin manifest, skill, agent, command markdown.

**Spec:** `docs/superpowers/specs/2026-08-27-gitlab-client-design.md` — read it first; Appendix A is the endpoint inventory every reference doc is built from.

## Global Constraints

- License **AGPL-3.0-only**: every new `.py` and `.sh`/bash file starts with `# SPDX-License-Identifier: AGPL-3.0-only` (after the shebang if any).
- Python floor **3.10** (`requires-python = ">=3.10"`); **zero runtime dependencies**; the CLI runs as `python3 -m gitlab_client` with `PYTHONPATH` set by `scripts/gl` — no venv at runtime.
- Dev dependency `pytest>=9.0`; build backend `uv_build>=0.11.7,<0.12`; tests live in `scripts/gitlab-client/tests/` with **no `__init__.py`** (pytest rootdir mode, matching `mysql-client`).
- Plugin version **0.1.0** in four places: `plugins/gitlab-client/.claude-plugin/plugin.json`, the entry in `.claude-plugin/marketplace.json`, `plugins/gitlab-client/scripts/gitlab-client/pyproject.toml`, `uv.lock` (`uv sync --locked`). Also `__version__` in `src/gitlab_client/__init__.py` (a test pins it to pyproject).
- Exit codes: **0** ok · **1** HTTP/API/network error · **2** usage/config error · **3** refused by write policy.
- The token is **never** printed, logged, or placed on a command line; error messages show `token_cmd`'s stderr and exit code only.
- HTTP verbs accepted by `gl api`: `GET`, `POST`, `PUT` only. POST/PUT allowed only on the spec §4.3 allow-list.
- Repo gates that must pass before the final commit: `claude plugin validate .`, `claude plugin validate plugins/gitlab-client`, `scripts/check-version-sync.sh`, `cd plugins/gitlab-client/scripts/gitlab-client && uv sync --locked --group dev && uv run pytest -q`.
- Work on branch `gitlab-client` (already created; the spec is its first commit). Commit after every task with the message shape used in this repo (`gitlab-client — <what>`), ending with the `Co-Authored-By` / `Claude-Session` trailers shown in the Bash tool description.

## File structure

```
.claude-plugin/marketplace.json                          (modify: add entry)
CLAUDE.md                                                 (modify: add plugin bullet)
plugins/gitlab-client/
  .claude-plugin/plugin.json                              manifest
  CLAUDE.md · README.md · setup-checklist.md
  commands/setup.md · commands/doctor.md
  agents/pipeline-debugger.md
  skills/gitlab-client/SKILL.md
  skills/gitlab-client/references/{local-settings,repo-browsing,merge-requests,pipelines,safety-perimeter,v15-compat}.md
  scripts/gl                                              bash launcher
  scripts/gitlab-client/pyproject.toml · uv.lock
  scripts/gitlab-client/src/gitlab_client/
    __init__.py      __version__
    __main__.py      sys.exit(main())
    errors.py        GlError(1) / ConfigError(2) / PolicyError(3) / HttpError(1)
    settings.py      parse_frontmatter · load_settings · resolve_token
    http.py          Client · Response · AuthStrippingRedirectHandler · parse_params · build_query · check_write_policy · substitute_project · project_fields · describe_error
    project.py       parse_remote_url · parse_project_ref · git_output/git_toplevel/git_remote_url · resolve_project · Project
    log.py           clean_line · parse_trace · Section · tail/head/grep · sections_summary · section_body · fetch_trace · header_line
    diff.py          file_status · render_file · render_diffs · render_files · render_compare · mr_diffs · commit_diff · compare
    artifacts.py     encode_artifact_path · download_archive · fetch_file · list_archive · extract_archive · cache paths
    cli.py           Context · build_parser · cmd_api/cmd_project/cmd_version/cmd_log/cmd_diff/cmd_artifacts · main
  scripts/gitlab-client/tests/
    conftest.py      StubOpener · FakeResponse · client fixture · run_gl helper
    test_version.py · test_settings.py · test_http.py · test_project.py · test_cli.py · test_log.py · test_diff.py · test_artifacts.py
    fixtures/trace.log · fixtures/artifacts.zip (built in-test)
docs/superpowers/specs/2026-08-27-gitlab-client-design.md   (exists)
```

---

### Task 1: Scaffold — manifests, launcher, package skeleton, errors

**Files:**
- Create: `plugins/gitlab-client/.claude-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json` (append to `plugins`)
- Modify: `CLAUDE.md` (repo root; plugin list)
- Create: `plugins/gitlab-client/scripts/gl`
- Create: `plugins/gitlab-client/scripts/gitlab-client/pyproject.toml` (+ `uv.lock`, generated by `uv sync` in Step 5 and committed)
- Create: `plugins/gitlab-client/scripts/gitlab-client/src/gitlab_client/__init__.py`, `__main__.py`, `errors.py`, `cli.py` (stub)
- Test: `plugins/gitlab-client/scripts/gitlab-client/tests/test_version.py`

**Interfaces:**
- Produces: `gitlab_client.__version__: str`; `gitlab_client.errors.{GlError, ConfigError, PolicyError, HttpError}` with `.exit_code`; `HttpError(status: int, method: str, url: str, detail: str)` with attributes of the same names; `gitlab_client.cli.main(argv) -> int` (stub returning 2 with usage for now).

- [ ] **Step 1: Write the manifest and marketplace entry**

`plugins/gitlab-client/.claude-plugin/plugin.json`:

```json
{
  "name": "gitlab-client",
  "version": "0.1.0",
  "description": "Drive an on-prem GitLab 15.x from Claude Code — browse repos, read and open merge requests, debug CI pipelines and job logs — through a bundled REST client with an allow-listed write policy.",
  "author": {
    "name": "aeresov"
  },
  "homepage": "https://github.com/aeresov/claude-plugins/tree/main/plugins/gitlab-client",
  "repository": "https://github.com/aeresov/claude-plugins",
  "keywords": ["gitlab", "merge-request", "ci", "pipeline", "self-managed", "rest-api"]
}
```

Append to the `plugins` array in `.claude-plugin/marketplace.json` (after the `pytest-httpchain` entry):

```json
    {
      "name": "gitlab-client",
      "source": "./plugins/gitlab-client",
      "description": "Drive an on-prem GitLab 15.x from Claude Code — browse repos, read and open merge requests, debug CI pipelines and job logs — through a bundled REST client with an allow-listed write policy.",
      "version": "0.1.0",
      "license": "AGPL-3.0-only",
      "homepage": "https://github.com/aeresov/claude-plugins/tree/main/plugins/gitlab-client",
      "repository": "https://github.com/aeresov/claude-plugins"
    }
```

In the repo-root `CLAUDE.md`, add to the `Plugins:` list:

```markdown
- [`gitlab-client`](plugins/gitlab-client/CLAUDE.md) — Drive an on-prem GitLab 15.x (repos, merge requests, CI/CD) through a bundled REST client; allow-listed writes, a `pipeline-debugger` subagent, `/setup` + `/doctor`.
```

- [ ] **Step 2: Write the launcher**

`plugins/gitlab-client/scripts/gl` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Launch the bundled gitlab_client package with the system python3 — no venv.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$here/gitlab-client/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m gitlab_client "$@"
```

- [ ] **Step 3: Write pyproject.toml**

`plugins/gitlab-client/scripts/gitlab-client/pyproject.toml`:

```toml
[project]
name = "gitlab-client"
version = "0.1.0"
description = "Small GitLab REST v4 client (gl) for the gitlab-client Claude Code plugin."
requires-python = ">=3.10"
license = "AGPL-3.0-only"
authors = [{ name = "aeresov" }]
keywords = ["gitlab", "rest", "cli"]
dependencies = []

[project.scripts]
gl = "gitlab_client.cli:main"

[dependency-groups]
dev = ["pytest>=9.0"]

[build-system]
requires = ["uv_build>=0.11.7,<0.12"]
build-backend = "uv_build"
```

- [ ] **Step 4: Write the failing version test**

`plugins/gitlab-client/scripts/gitlab-client/tests/test_version.py`:

```python
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
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd plugins/gitlab-client/scripts/gitlab-client && uv sync --group dev && uv run pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client'`

- [ ] **Step 6: Write the package skeleton**

`src/gitlab_client/__init__.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""gitlab-client — a small GitLab REST v4 client for the gitlab-client Claude Code plugin."""

__version__ = "0.1.0"
```

`src/gitlab_client/errors.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""Exception hierarchy. Each class carries the process exit code `cli.main` returns."""


class GlError(Exception):
    """Base: message goes to stderr, process exits 1."""

    exit_code = 1


class ConfigError(GlError):
    """Settings, token, project-resolution, or usage problems — exit 2."""

    exit_code = 2


class PolicyError(GlError):
    """Refused by the write policy before any network call — exit 3."""

    exit_code = 3


class HttpError(GlError):
    """A non-2xx response (after the single 429 retry)."""

    def __init__(self, status: int, method: str, url: str, detail: str):
        self.status, self.method, self.url, self.detail = status, method, url, detail
        super().__init__(f"HTTP {status} {method} {url}: {detail}")
```

`src/gitlab_client/__main__.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
```

`src/gitlab_client/cli.py` (stub — replaced in Task 6):

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""argparse front-end for `gl`. Filled in by later tasks."""
from __future__ import annotations

import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    print("usage: gl {api,project,log,diff,artifacts,version} ...", file=sys.stderr)
    return 2
```

- [ ] **Step 7: Run tests + gates to verify they pass**

Run: `cd plugins/gitlab-client/scripts/gitlab-client && uv sync --group dev && uv run pytest -q`
Expected: `2 passed`

Run (repo root): `chmod +x plugins/gitlab-client/scripts/gl && plugins/gitlab-client/scripts/gl; echo "exit=$?"`
Expected: the usage line on stderr and `exit=2`.

Run: `claude plugin validate . && claude plugin validate plugins/gitlab-client && scripts/check-version-sync.sh`
Expected: all succeed (validate may warn that the plugin has no components yet — that's fine; it must not error).

Run: `cd plugins/gitlab-client/scripts/gitlab-client && uv sync --locked --group dev`
Expected: succeeds — `uv.lock` was generated by Step 5 and is committed with this task (CI runs `--locked` from the first push).

- [ ] **Step 8: Commit**

```bash
git add .claude-plugin/marketplace.json CLAUDE.md plugins/gitlab-client
git commit -m "gitlab-client 0.1.0 — scaffold: manifest, gl launcher, package skeleton"
```

---

### Task 2: settings.py — frontmatter, layered settings, token resolution

**Files:**
- Create: `src/gitlab_client/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces:
  - `parse_frontmatter(text: str) -> dict[str, str]`
  - `read_file_settings(path: Path) -> dict[str, str]` (warns on stderr for unknown keys, returns only known)
  - `@dataclass Settings(url: str, token_cmd: str|None, project: str|None, token: str|None, sources: dict[str,str], user_file: Path|None, project_file: Path|None)`
  - `load_settings(*, cwd: Path, home: Path, env: Mapping[str,str], url_flag: str|None = None, project_flag: str|None = None) -> Settings` — raises `ConfigError`
  - `resolve_token(settings: Settings, run=subprocess.run) -> str` — raises `ConfigError`
  - Constants `USER_FILE = Path(".claude/gitlab-client.local.md")`, `PROJECT_FILE` (same relative path), `ENV_URL = "GITLAB_CLIENT_URL"`, `ENV_TOKEN = "GITLAB_CLIENT_TOKEN"`, `KNOWN_KEYS = ("url", "token_cmd", "project")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
import subprocess
from pathlib import Path

import pytest

from gitlab_client.errors import ConfigError
from gitlab_client.settings import (
    ENV_TOKEN,
    ENV_URL,
    Settings,
    load_settings,
    parse_frontmatter,
    resolve_token,
)


def test_parse_plain_and_quoted_values():
    text = "---\n# comment\nurl: https://gitlab.example.com/\ntoken_cmd: \"ksm secret get x\"\n---\n\n# notes\nurl: not-parsed\n"
    assert parse_frontmatter(text) == {"url": "https://gitlab.example.com/", "token_cmd": "ksm secret get x"}


def test_parse_block_scalar():
    text = "---\ntoken_cmd: |\n  export A=1\n  echo $A\n\nproject: g/p\n---\n"
    assert parse_frontmatter(text) == {"token_cmd": "export A=1\necho $A", "project": "g/p"}


def test_parse_without_frontmatter_is_empty():
    assert parse_frontmatter("# just a heading\nurl: x\n") == {}
    assert parse_frontmatter("") == {}


def write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_precedence_flag_env_project_user(tmp_path, capsys):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    write(home / ".claude/gitlab-client.local.md", "---\nurl: https://user.example\ntoken_cmd: echo u\nbogus: 1\n---\n")
    write(cwd / ".claude/gitlab-client.local.md", "---\nurl: https://proj.example\nproject: grp/proj\n---\n")

    s = load_settings(cwd=cwd, home=home, env={})
    assert (s.url, s.token_cmd, s.project) == ("https://proj.example", "echo u", "grp/proj")
    assert s.sources == {"url": "project file", "token_cmd": "user file", "project": "project file"}
    assert s.user_file == home / ".claude/gitlab-client.local.md"
    assert s.project_file == cwd / ".claude/gitlab-client.local.md"
    assert "unknown setting 'bogus'" in capsys.readouterr().err

    s = load_settings(cwd=cwd, home=home, env={ENV_URL: "https://env.example/"})
    assert s.url == "https://env.example"  # trailing slash stripped
    assert s.sources["url"] == "env"

    s = load_settings(cwd=cwd, home=home, env={ENV_URL: "https://env.example"}, url_flag="https://flag.example", project_flag="a/b")
    assert (s.url, s.project, s.sources["url"], s.sources["project"]) == ("https://flag.example", "a/b", "flag", "flag")


def test_env_token_bypasses_token_cmd(tmp_path):
    s = load_settings(cwd=tmp_path, home=tmp_path, env={ENV_URL: "https://x", ENV_TOKEN: "sekrit"})
    assert s.token == "sekrit" and s.token_cmd is None
    assert resolve_token(s, run=lambda *a, **k: pytest.fail("token_cmd must not run")) == "sekrit"


def test_missing_url_and_token_cmd(tmp_path):
    with pytest.raises(ConfigError, match="no GitLab URL configured.*gitlab-client:setup"):
        load_settings(cwd=tmp_path, home=tmp_path, env={})
    with pytest.raises(ConfigError, match="no token_cmd configured"):
        load_settings(cwd=tmp_path, home=tmp_path, env={ENV_URL: "https://x"})
    with pytest.raises(ConfigError, match="must start with http"):
        load_settings(cwd=tmp_path, home=tmp_path, env={ENV_URL: "gitlab.example.com", ENV_TOKEN: "t"})


def fake_run(stdout="", stderr="", returncode=0, raise_timeout=False):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd, 60)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    run.calls = calls
    return run


def settings(token_cmd="printf %s tok"):
    return Settings(url="https://x", token_cmd=token_cmd, project=None, token=None, sources={})


def test_resolve_token_runs_shell_command():
    run = fake_run(stdout="glpat-abc\n")
    assert resolve_token(settings("ksm secret get x"), run=run) == "glpat-abc"
    cmd, kwargs = run.calls[0]
    assert cmd == "ksm secret get x"
    assert kwargs["shell"] is True and kwargs["capture_output"] is True and kwargs["timeout"] == 60


def test_resolve_token_failures_never_show_stdout():
    with pytest.raises(ConfigError) as e:
        resolve_token(settings(), run=fake_run(stdout="glpat-leak", stderr="not logged in", returncode=3))
    assert "exited 3" in str(e.value) and "not logged in" in str(e.value) and "glpat-leak" not in str(e.value)

    with pytest.raises(ConfigError, match="exactly one non-empty line \\(got 2\\)"):
        resolve_token(settings(), run=fake_run(stdout="line1\nline2\n"))
    with pytest.raises(ConfigError, match="got 0"):
        resolve_token(settings(), run=fake_run(stdout="\n"))
    with pytest.raises(ConfigError, match="timed out"):
        resolve_token(settings(), run=fake_run(raise_timeout=True))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_settings.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client.settings'`

- [ ] **Step 3: Implement settings.py**

`src/gitlab_client/settings.py`:

```python
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


def read_file_settings(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = parse_frontmatter(path.read_text(encoding="utf-8"))
    for key in data:
        if key not in KNOWN_KEYS:
            print(f"gl: warning: {path}: unknown setting '{key}' ignored", file=sys.stderr)
    return {k: v for k, v in data.items() if k in KNOWN_KEYS and v}


@dataclass
class Settings:
    url: str
    token_cmd: str | None = None
    project: str | None = None
    token: str | None = None  # from GITLAB_CLIENT_TOKEN; when set, token_cmd is never run
    sources: dict[str, str] = field(default_factory=dict)
    user_file: Path | None = None
    project_file: Path | None = None


def load_settings(
    *,
    cwd: Path,
    home: Path,
    env: Mapping[str, str],
    url_flag: str | None = None,
    project_flag: str | None = None,
) -> Settings:
    user_path = home / USER_FILE
    project_path = cwd / PROJECT_FILE
    layers = [
        ("user file", read_file_settings(user_path)),
        ("project file", read_file_settings(project_path)),
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
        user_file=user_path if user_path.is_file() else None,
        project_file=project_path if project_path.is_file() else None,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_settings.py`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — settings: frontmatter parser, layered settings, token_cmd resolution"
```

---

### Task 3: http.py — transport: auth, URL building, errors, 429, redirects, streaming

**Files:**
- Create: `src/gitlab_client/http.py`
- Create: `tests/conftest.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: `gitlab_client.__version__`, `errors.{GlError, HttpError, ConfigError, PolicyError}`.
- Produces:
  - `build_query(params: Mapping[str, Any]) -> str` — bools → `true`/`false`, lists → `key[]=`, `None` skipped.
  - `@dataclass Response(status: int, headers: dict[str,str] (lower-cased keys), body: bytes, url: str, bytes_written: int = 0)` with `.json() -> Any` and `.header(name, default="") -> str`.
  - `class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler)` — `__init__(api_host: str)`.
  - `class Client` — `__init__(base_url: str, token: str, *, opener=None, sleep=time.sleep)`; attributes `base_url`, `api` (`<base>/api/v4`), `host`; `url_for(path, query=None) -> str`; `request(method, path, *, query=None, json_body=None, accept_json=True, timeout=None, stream_to=None, absolute_url=None) -> Response`.
  - `describe_error(status: int, body: bytes) -> str`.
  - Constants `JSON_TIMEOUT = 30`, `DOWNLOAD_TIMEOUT = 300`, `MAX_RETRY_AFTER = 30`.
- Task 4 adds `parse_params`, `check_write_policy`, `substitute_project`, `Client.paginate`, `project_fields` to this same module.

- [ ] **Step 1: Write conftest.py**

`tests/conftest.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_http.py` (Task 4 appends more tests to this file):

```python
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
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest -q tests/test_http.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client.http'` (conftest import fails, so the whole session errors — that's the expected failure).

- [ ] **Step 4: Implement http.py (transport half)**

`src/gitlab_client/http.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""Thin urllib client: auth, encoding, pagination, redirects, error mapping, write policy.

Everything network-related lives here so `cli.py` and the helpers stay testable with a
stub opener (see tests/conftest.py).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from . import __version__
from .errors import ConfigError, GlError, HttpError, PolicyError

JSON_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300
MAX_RETRY_AFTER = 30
PER_PAGE = 100
CHUNK = 1 << 20
AUTH_HEADERS = ("Private-token", "Authorization")  # urllib capitalises header names this way
LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
KEYSET_PATHS = re.compile(r"/repository/tree/?$")

# Hints appended to error messages by status code (spec §8).
_HINTS = {
    401: " — token rejected; check token_cmd output and the token's expiry (/gitlab-client:doctor)",
    403: " — forbidden: role too low (Developer to retry/cancel/play/trigger, Reporter to read logs), a protected branch, or a missing token scope",
    404: " — GitLab returns 404 for both missing and not-visible resources; check the project path and the token's access",
    429: " — rate limited (already retried once after Retry-After)",
}


def _scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def build_query(params: Mapping[str, Any]) -> str:
    """Encode a params mapping: bools → true/false, lists → key[]=v repeated, None skipped."""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((f"{key}[]", _scalar(v)) for v in value)
        elif value is not None:
            pairs.append((key, _scalar(value)))
    return urllib.parse.urlencode(pairs)


def describe_error(status: int, body: bytes) -> str:
    """Turn an error body (JSON `message`/`error` shapes, or plain text) into one line."""
    text = body.decode("utf-8", errors="replace").strip()
    data: Any = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict):
        if data.get("error") == "insufficient_scope":
            return f"token lacks the '{data.get('scope', '?')}' scope ({data.get('error_description', '')})"
        msg = data.get("message", data.get("error"))
        if msg is None:
            msg = text
        elif not isinstance(msg, str):
            msg = json.dumps(msg)
    else:
        msg = text[:200] or "(empty body)"
    return msg + _HINTS.get(status, "")


@dataclass
class Response:
    status: int
    headers: dict[str, str]  # lower-cased keys
    body: bytes
    url: str
    bytes_written: int = 0

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body.strip() else None

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower()) or default


class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but drop auth headers when the target host differs from the API host.

    GitLab 302s artifact/trace downloads to pre-signed object-storage URLs; the token must
    not travel there.
    """

    def __init__(self, api_host: str):
        super().__init__()
        self.api_host = api_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc.lower() != self.api_host:
            for name in AUTH_HEADERS:
                new.remove_header(name)
        return new


def _retry_after(headers: Any) -> float:
    try:
        return float(headers.get("Retry-After", "1"))
    except (TypeError, ValueError, AttributeError):
        return 1.0


class Client:
    def __init__(self, base_url: str, token: str, *, opener: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.base_url = base_url.rstrip("/")
        self.api = self.base_url + "/api/v4"
        self.host = urllib.parse.urlsplit(self.base_url).netloc
        self._token = token
        self._sleep = sleep
        self._opener = opener or urllib.request.build_opener(AuthStrippingRedirectHandler(self.host))

    def url_for(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        url = self.api + (path if path.startswith("/") else "/" + path)
        qs = build_query(query) if query else ""
        if qs:
            url += ("&" if "?" in url else "?") + qs
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        accept_json: bool = True,
        timeout: float | None = None,
        stream_to: Any = None,
        absolute_url: str | None = None,
    ) -> Response:
        method = method.upper()
        url = absolute_url or self.url_for(path, query)
        headers = {"User-Agent": f"gitlab-client/{__version__} (claude-code plugin)"}
        if accept_json:
            headers["Accept"] = "application/json"
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        req.add_header("PRIVATE-TOKEN", self._token)
        timeout = timeout or (DOWNLOAD_TIMEOUT if stream_to is not None else JSON_TIMEOUT)

        for attempt in (1, 2):
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    if stream_to is None:
                        return Response(resp.status, hdrs, resp.read(), url)
                    written = 0
                    while chunk := resp.read(CHUNK):
                        stream_to.write(chunk)
                        written += len(chunk)
                    return Response(resp.status, hdrs, b"", url, bytes_written=written)
            except urllib.error.HTTPError as e:
                body = e.fp.read() if e.fp is not None else b""
                if e.code == 429 and attempt == 1:
                    self._sleep(min(_retry_after(e.headers), MAX_RETRY_AFTER))
                    continue
                raise HttpError(e.code, method, url, describe_error(e.code, body)) from None
            except urllib.error.URLError as e:
                reason = e.reason
                raise GlError(f"{method} {url}: {type(reason).__name__}: {reason}") from None
            except OSError as e:  # socket timeouts and friends raised directly
                raise GlError(f"{method} {url}: {type(e).__name__}: {e}") from None
        raise AssertionError("unreachable")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_http.py`
Expected: `9 passed`

- [ ] **Step 6: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — http: urllib transport with auth, error mapping, 429 retry, auth-stripping redirects"
```

---

### Task 4: http.py — params, `:project`, write allow-list, pagination, `--fields`

**Files:**
- Modify: `src/gitlab_client/http.py` (append)
- Test: `tests/test_http.py` (append)

**Interfaces:**
- Produces (all in `gitlab_client.http`):
  - `parse_params(items: Iterable[str]) -> dict[str, Any]` — `k=v` → str, `k:=json` → parsed, `k[]=v` → list; raises `ConfigError`.
  - `encode_path_segment(value: str) -> str` — `urllib.parse.quote(value, safe="")`.
  - `substitute_project(path: str, project_path: str | None) -> str` — raises `ConfigError` if `:project` present and no project.
  - `check_write_policy(method: str, path: str) -> None` — raises `PolicyError`.
  - `WRITE_ALLOW: list[re.Pattern]`.
  - `Client.paginate(path: str, query: Mapping[str, Any] | None, *, max_items: int = 1000, warn: Callable[[str], None] = lambda m: None) -> list[Any]`.
  - `project_fields(data: Any, fields: list[str]) -> Any` — dotted paths; an all-digit segment indexes a list (`notes.0.body`); missing → `None`.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_http.py`:

```python


# ---- Task 4: params, policy, pagination, fields -------------------------------------------
from gitlab_client.errors import ConfigError, PolicyError  # noqa: E402
from gitlab_client.http import (  # noqa: E402
    check_write_policy,
    encode_path_segment,
    parse_params,
    project_fields,
    substitute_project,
)


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_http.py`
Expected: FAIL — `ImportError: cannot import name 'check_write_policy'`

- [ ] **Step 3: Append the implementation**

Append to `src/gitlab_client/http.py`:

```python


# ---- parameters, project placeholder, write policy ------------------------------------------

_PARAM = re.compile(r"^([A-Za-z0-9_.\-\[\]]+)(:=|=)(.*)$", re.S)


def parse_params(items: Iterable[str]) -> dict[str, Any]:
    """`key=value` → str; `key:=json` → parsed JSON; `key[]=value` → list (repeatable)."""
    out: dict[str, Any] = {}
    for item in items:
        m = _PARAM.match(item)
        if not m:
            raise ConfigError(f"parameter must be key=value, key:=json or key[]=value: {item!r}")
        key, op, raw = m.groups()
        if op == ":=":
            try:
                value: Any = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ConfigError(f"bad JSON for {key}: {e}") from None
        else:
            value = raw
        if key.endswith("[]"):
            out.setdefault(key[:-2], []).append(value)
        else:
            out[key] = value
    return out


def encode_path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def substitute_project(path: str, project_path: str | None) -> str:
    if ":project" not in path:
        return path
    if not project_path:
        raise ConfigError("path uses :project but no project could be resolved")
    return path.replace(":project", encode_path_segment(project_path))


_P = r"/projects/[^/]+"
_MR = _P + r"/merge_requests/\d+"
_DISC = _MR + r"/discussions/[0-9a-fA-F]+"
WRITE_ALLOW = [
    re.compile(p)
    for p in (
        rf"^POST {_P}/merge_requests$",
        rf"^PUT {_MR}$",
        rf"^POST {_MR}/notes$",
        rf"^POST {_MR}/discussions$",
        rf"^POST {_DISC}/notes$",
        rf"^PUT {_DISC}$",
        rf"^POST {_P}/pipeline$",
        rf"^POST {_P}/pipelines/\d+/(retry|cancel)$",
        rf"^POST {_P}/jobs/\d+/(retry|cancel|play)$",
        rf"^POST {_P}/jobs/\d+/artifacts/keep$",
    )
]


def check_write_policy(method: str, path: str) -> None:
    """GET always passes; POST/PUT must match WRITE_ALLOW; every other verb is refused."""
    method = method.upper()
    if method == "GET":
        return
    if method not in ("POST", "PUT"):
        raise PolicyError(f"{method} is not supported by gl (only GET, POST, PUT)")
    key = f"{method} {path.split('?', 1)[0].rstrip('/')}"
    if not any(p.match(key) for p in WRITE_ALLOW):
        raise PolicyError(f"refused by gitlab-client write policy: {key} (see references/safety-perimeter.md)")


# ---- pagination and projection --------------------------------------------------------------


def _paginate(self: Client, path: str, query: Mapping[str, Any] | None, *, max_items: int = 1000,
              warn: Callable[[str], None] = lambda m: None) -> list[Any]:
    """Follow pagination and return one concatenated list.

    Offset endpoints: per_page=100, loop on the x-next-page header. /repository/tree is
    keyset-only on GitLab ≥ 15.0: pagination=keyset and follow the Link rel="next" URL.
    """
    q: dict[str, Any] = dict(query or {})
    q["per_page"] = PER_PAGE
    keyset = bool(KEYSET_PATHS.search(path.split("?", 1)[0]))
    if keyset:
        q["pagination"] = "keyset"
    else:
        q["page"] = 1
    items: list[Any] = []
    next_url: str | None = None
    while True:
        resp = self.request("GET", path, absolute_url=next_url) if next_url else self.request("GET", path, query=q)
        page = resp.json()
        if not isinstance(page, list):
            raise GlError(f"--all expects an array response, got {type(page).__name__}")
        items.extend(page)
        if keyset:
            m = LINK_NEXT.search(resp.header("link"))
            has_next = bool(m and page)
            next_url = m.group(1) if has_next else None
        else:
            nxt = resp.header("x-next-page")
            if nxt and page:
                has_next, next_url = True, None
                q["page"] = int(nxt)
            else:  # some proxies drop x-* headers; fall back to the Link header
                m = LINK_NEXT.search(resp.header("link"))
                has_next = bool(m and page)
                next_url = m.group(1) if has_next else None
        if len(items) >= max_items:
            if len(items) > max_items or has_next:
                warn(f"gl: warning: --all stopped at --max {max_items} items; more are available")
            return items[:max_items]
        if not has_next:
            return items


Client.paginate = _paginate  # type: ignore[attr-defined]


def project_fields(data: Any, fields: list[str]) -> Any:
    """Keep only the listed dotted paths of each object; missing → None."""

    def pick(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return obj
        out: dict[str, Any] = {}
        for f in fields:
            cur: Any = obj
            for part in f.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                elif isinstance(cur, list) and part.isdigit():
                    cur = cur[int(part)] if int(part) < len(cur) else None
                else:
                    cur = None
                if cur is None:
                    break
            out[f] = cur
        return out

    return [pick(x) for x in data] if isinstance(data, list) else pick(data)
```

(`Client.paginate = _paginate` keeps the method next to its helpers; if you prefer, move the body into the class — the tests only care about `client.paginate(...)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_http.py`
Expected: all pass (9 from Task 3 + 35 parametrised here + 10 = 54)

- [ ] **Step 5: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — http: typed params, :project placeholder, write allow-list, offset/keyset pagination, --fields"
```

---

### Task 5: project.py — git remote → project path → GET /projects/:path

**Files:**
- Create: `src/gitlab_client/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `http.Client.request`, `http.encode_path_segment`, `settings.Settings`, `errors.{ConfigError, HttpError}`.
- Produces:
  - `parse_remote_url(url: str, url_prefix: str = "") -> str | None`
  - `parse_project_ref(ref: str, base_url: str) -> str`
  - `git_output(args: list[str], cwd: Path, run=subprocess.run) -> str | None`
  - `git_toplevel(cwd: Path, run=subprocess.run) -> Path | None`
  - `git_remote_url(cwd: Path, remote: str = "origin", run=subprocess.run) -> str | None`
  - `@dataclass Project(id: int, path: str, default_branch: str, web_url: str, resolved_from: str)`
  - `resolve_project(client: Client, settings: Settings, *, remote: str, cwd: Path, run=subprocess.run) -> Project`

- [ ] **Step 1: Write the failing tests**

`tests/test_project.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_project.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client.project'`

- [ ] **Step 3: Implement project.py**

`src/gitlab_client/project.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve which GitLab project a call is about: --project flag → settings → git remote.

Only the remote's *path* matters (SSH aliases and split hostnames are common); the
GET /projects/:path confirmation call is the check that the guess was right.
"""
from __future__ import annotations

import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
        path = parse_project_ref(settings.project, settings.url) if source == "flag" else _clean(settings.project)
    else:
        remote_url = git_remote_url(cwd, remote, run)
        if not remote_url:
            raise ConfigError(
                f"cannot resolve project: no --project, no project: in settings, and "
                f"`git remote get-url {remote}` gave nothing"
            )
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
                f"project {path!r} (from {source}) not found or not visible to this token — "
                f"pass --project, or set project: in .claude/gitlab-client.local.md"
            ) from None
        raise
    return Project(
        id=int(data["id"]),
        path=data["path_with_namespace"],
        default_branch=data.get("default_branch") or "",
        web_url=data.get("web_url") or "",
        resolved_from=source,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_project.py`
Expected: `18 passed`

- [ ] **Step 5: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — project: git remote / URL / settings → GET /projects/:path"
```

---

### Task 6: cli.py — Context, parser, `gl api`, `gl project`, `gl version`

**Files:**
- Modify: `src/gitlab_client/cli.py` (replace the stub)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces:
  - `class Context(args, *, cwd: Path, home: Path, env: Mapping[str,str], client_factory, stderr)` with lazy properties `settings: Settings`, `client: Client`, `project: Project`, plus `host: str`, `cache_dir: Path` (`$XDG_CACHE_HOME or ~/.cache` / `gitlab-client` / `<host>`), `warn(msg: str)`.
  - `build_parser() -> argparse.ArgumentParser` — subcommands `api`, `project`, `version` now; `log`, `diff`, `artifacts` are added in Tasks 7–9 (their parser sections are written here already, with their `cmd_*` functions arriving in those tasks — until then `COMMANDS` maps them to a function that raises `ConfigError("not implemented yet")`).
  - `main(argv=None, *, cwd=None, home=None, env=None, client_factory=None, stdout=None, stderr=None) -> int`.
  - `COMMANDS: dict[str, Callable[[Context, argparse.Namespace, TextIO], int]]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
import json

import pytest

PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "https://gitlab.example.com/group/proj"}


def test_no_command_is_usage(run_gl):
    code, out, err = run_gl()
    assert code == 2 and "usage:" in err


def test_api_get_with_project_placeholder_and_fields(run_gl, opener):
    opener.add(200, PROJECT_JSON).add(200, [{"iid": 1, "title": "A", "author": {"username": "u"}, "big": "x" * 100}])
    code, out, err = run_gl("api", "GET", "/projects/:project/merge_requests", "state=opened",
                            "--project", "group/proj", "--fields", "iid,title,author.username")
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


def test_api_delete_is_usage_error(run_gl):
    with pytest.raises(SystemExit):
        run_gl("api", "DELETE", "/projects/1")


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
    assert json.loads(out) == {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main",
                               "web_url": "https://gitlab.example.com/group/proj", "resolved_from": "flag"}


def test_version_warns_on_non_15(run_gl, opener):
    opener.add(200, {"version": "15.11.13-ee", "revision": "abc", "enterprise": True})
    code, out, err = run_gl("version")
    assert code == 0 and err == ""
    assert json.loads(out) == {"version": "15.11.13-ee", "revision": "abc", "enterprise": True, "url": "https://gitlab.example.com"}
    assert opener.last.full_url.endswith("/api/v4/metadata")

    opener.add(200, {"version": "16.4.0", "revision": "abc", "enterprise": True})
    code, out, err = run_gl("version")
    assert code == 0 and "written for GitLab 15.11; this instance is 16.4.0" in err

    opener.add(200, {"version": "16.4.0", "revision": "abc", "enterprise": True})
    code, out, err = run_gl("version", "--quiet")
    assert code == 0 and err == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_cli.py`
Expected: FAIL — `TypeError: main() got an unexpected keyword argument 'cwd'`

- [ ] **Step 3: Replace cli.py**

`src/gitlab_client/cli.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""argparse front-end for `gl`.

Subcommands: api, project, version (this task); log, diff, artifacts (Tasks 7–9).
All network access goes through `Context.client`; all inputs (cwd, home, env, the client
factory, stdio) are injectable so tests never touch the real environment.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from . import __version__
from .errors import ConfigError, GlError, PolicyError
from .http import Client, check_write_policy, parse_params, project_fields, substitute_project
from .project import Project, git_toplevel, resolve_project
from .settings import Settings, load_settings, resolve_token

ClientFactory = Callable[[str, str], Client]
Command = Callable[["Context", argparse.Namespace, TextIO], int]


class Context:
    """Lazily resolves settings → token → client → project, each at most once per process."""

    def __init__(self, args: argparse.Namespace, *, cwd: Path, home: Path, env: Mapping[str, str],
                 client_factory: ClientFactory, stderr: TextIO):
        self.args = args
        self.cwd = cwd
        self.home = home
        self.env = env
        self.stderr = stderr
        self.root = git_toplevel(cwd) or cwd
        self._client_factory = client_factory
        self._settings: Settings | None = None
        self._client: Client | None = None
        self._project: Project | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings(
                cwd=self.root, home=self.home, env=self.env,
                url_flag=getattr(self.args, "url", None), project_flag=getattr(self.args, "project", None),
            )
        return self._settings

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self._client_factory(self.settings.url, resolve_token(self.settings))
        return self._client

    @property
    def project(self) -> Project:
        if self._project is None:
            self._project = resolve_project(self.client, self.settings, remote=getattr(self.args, "remote", "origin"), cwd=self.cwd)
        return self._project

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.settings.url).netloc

    @property
    def cache_dir(self) -> Path:
        base = Path(self.env.get("XDG_CACHE_HOME") or (self.home / ".cache"))
        return base / "gitlab-client" / self.host

    def warn(self, message: str) -> None:
        if not getattr(self.args, "quiet", False):
            print(message, file=self.stderr)


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---- commands --------------------------------------------------------------------------------


RAW_ROUTE = re.compile(r"/(raw|trace|artifacts(/.*)?|archive(\.[a-z0-9.]+)?)$")


def cmd_api(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    method = args.method
    # Policy first, on the *unsubstituted* path: a refused write must never run token_cmd or touch the network.
    check_write_policy(method, args.path)
    params = parse_params(args.params)
    if args.all and method != "GET":
        raise ConfigError("--all only applies to GET")

    body: Any = None
    if args.json_body is not None:
        try:
            body = json.loads(args.json_body)
        except json.JSONDecodeError as e:
            raise ConfigError(f"--json: bad JSON: {e}") from None
    elif method != "GET":
        body = params
    query = params if method == "GET" else None
    if "sudo" in params or (isinstance(body, dict) and "sudo" in body):
        raise PolicyError("refused by gitlab-client write policy: the sudo parameter (impersonation) is not allowed")

    path = substitute_project(args.path, ctx.project.path) if ":project" in args.path else args.path
    accept_json = not RAW_ROUTE.search(path.split("?", 1)[0])

    if args.out:
        with open(args.out, "wb") as fh:
            resp = ctx.client.request(method, path, query=query, json_body=body, accept_json=False, stream_to=fh)
        out.write(f"wrote {resp.bytes_written} bytes to {args.out}\n")
        return 0

    if args.all:
        data = ctx.client.paginate(path, query, max_items=args.max, warn=ctx.warn)
    else:
        resp = ctx.client.request(method, path, query=query, json_body=body, accept_json=accept_json)
        try:
            data = resp.json()
        except ValueError:
            out.write(resp.body.decode("utf-8", errors="replace"))
            return 0
    if args.fields:
        data = project_fields(data, [f.strip() for f in args.fields.split(",") if f.strip()])
    out.write(_dump(data))
    return 0


def cmd_project(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    p = ctx.project
    out.write(_dump({"id": p.id, "path_with_namespace": p.path, "default_branch": p.default_branch, "web_url": p.web_url, "resolved_from": p.resolved_from}))
    return 0


def cmd_version(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    data = ctx.client.request("GET", "/metadata").json() or {}
    version = str(data.get("version", "?"))
    if version.split(".", 1)[0] != "15":
        ctx.warn(f"gl: warning: references are written for GitLab 15.11; this instance is {version}")
    out.write(_dump({"version": version, "revision": data.get("revision"), "enterprise": data.get("enterprise"), "url": ctx.settings.url}))
    return 0


def _not_implemented(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    raise ConfigError(f"{args.command} is not implemented yet")


COMMANDS: dict[str, Command] = {
    "api": cmd_api,
    "project": cmd_project,
    "version": cmd_version,
    "log": _not_implemented,
    "diff": _not_implemented,
    "artifacts": _not_implemented,
}


# ---- parser ----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", help="GitLab base URL (overrides settings)")
    common.add_argument("--project", help="group/name or a GitLab URL (overrides settings and the git remote)")
    common.add_argument("--remote", default="origin", help="git remote to derive the project from (default: origin)")
    common.add_argument("-q", "--quiet", action="store_true", help="suppress warnings on stderr (errors still print)")

    p = argparse.ArgumentParser(prog="gl", description="GitLab REST v4 client for the gitlab-client Claude Code plugin.")
    p.add_argument("--version", action="version", version=f"gl {__version__}")
    sub = p.add_subparsers(dest="command", metavar="{api,project,log,diff,artifacts,version}")

    a = sub.add_parser("api", parents=[common], help="call any REST v4 endpoint (GET/POST/PUT; writes are allow-listed)")
    a.add_argument("method", type=str.upper, choices=["GET", "POST", "PUT"])
    a.add_argument("path", help="path under /api/v4; ':project' expands to the URL-encoded project path")
    a.add_argument("params", nargs="*", help="key=value | key:=json | key[]=value (query for GET, JSON body for POST/PUT)")
    a.add_argument("--all", action="store_true", help="follow pagination and print one array (GET only)")
    a.add_argument("--max", type=int, default=1000, help="item cap for --all (default 1000)")
    a.add_argument("--fields", help="comma-separated dotted paths to keep in each object; `.N` indexes a list (author.username, notes.0.body)")
    a.add_argument("--out", help="stream the raw response body to FILE")
    a.add_argument("--json", dest="json_body", help="raw JSON request body (POST/PUT) instead of params")

    sub.add_parser("project", parents=[common], help="print the resolved project")
    sub.add_parser("version", parents=[common], help="GET /metadata (warns if not GitLab 15.x)")

    lg = sub.add_parser("log", parents=[common], help="job log: ANSI-stripped, bounded views")
    lg.add_argument("job_id", type=int)
    lg.add_argument("--tail", type=int, help="last N lines (default 150; 0 = whole log, with a warning)")
    lg.add_argument("--head", type=int, help="first N lines")
    lg.add_argument("--grep", metavar="REGEX", help="case-insensitive search; prints 'line: text'")
    lg.add_argument("-C", "--context", type=int, default=0, help="context lines around --grep hits")
    lg.add_argument("--sections", action="store_true", help="list runner sections with line counts and durations")
    lg.add_argument("--section", metavar="NAME", help="restrict to one section")
    lg.add_argument("--raw", action="store_true", help="keep ANSI escape sequences")
    lg.add_argument("--refresh", action="store_true", help="re-download even if cached")

    d = sub.add_parser("diff", parents=[common], help="unified diff of an MR, a commit, or a ref range")
    d.add_argument("mr_iid", nargs="?", type=int)
    d.add_argument("--commit", metavar="SHA")
    d.add_argument("--range", metavar="FROM..TO")
    d.add_argument("--straight", action="store_true", help="two-dot compare (default is merge-base)")
    d.add_argument("--files", action="store_true", help="list changed files instead of the diff")
    d.add_argument("--file", metavar="PATH", help="only this file")

    ar = sub.add_parser("artifacts", parents=[common], help="download/list/extract job artifacts")
    ar.add_argument("job_id", nargs="?", type=int)
    ar.add_argument("--ref", help="branch or tag (latest successful pipeline); needs --job")
    ar.add_argument("--job", help="job name, with --ref")
    ar.add_argument("--list", action="store_true", help="list archive entries")
    ar.add_argument("--extract", metavar="DIR", help="extract the archive into DIR")
    ar.add_argument("--file", metavar="PATH", help="fetch one file from the artifacts (no archive download)")
    ar.add_argument("--out", help="with --file: write to FILE instead of stdout")
    ar.add_argument("--refresh", action="store_true", help="re-download even if cached")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: ClientFactory | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        if args.command == "api" and not any(x.startswith("-") for x in extra):
            args.params.extend(extra)  # params given after options, e.g. `gl api GET /p --fields id state=opened`
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if not args.command:
        parser.print_usage(stderr)
        return 2
    ctx = Context(
        args,
        cwd=Path(cwd or os.getcwd()),
        home=Path(home or Path.home()),
        env=os.environ if env is None else env,
        client_factory=client_factory or Client,
        stderr=stderr,
    )
    try:
        return COMMANDS[args.command](ctx, args, stdout)
    except GlError as e:
        print(f"gl: {e}", file=stderr)
        return e.exit_code
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass (`test_cli.py`: 14 passed; the earlier suites unchanged)

Run a real invocation through the launcher from the repo root (no settings → exit 2):
`plugins/gitlab-client/scripts/gl api GET /user; echo "exit=$?"`
Expected: `gl: no GitLab URL configured — run /gitlab-client:setup to create /home/<you>/.claude/gitlab-client.local.md (or set GITLAB_CLIENT_URL)` and `exit=2`.

- [ ] **Step 5: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — cli: gl api / project / version with lazy settings→token→client→project context"
```

---

### Task 7: log.py + `gl log` — trace download, ANSI/section cleanup, bounded views

**Files:**
- Create: `src/gitlab_client/log.py`
- Modify: `src/gitlab_client/cli.py` (add `cmd_log`, register in `COMMANDS`)
- Test: `tests/test_log.py`

**Interfaces:**
- Consumes: `http.Client.request` (with `stream_to`), `cli.Context.{client, project, cache_dir, warn}`.
- Produces (`gitlab_client.log`):
  - `ANSI_RE`, `SECTION_RE`, `TERMINAL = {"success", "failed", "canceled", "skipped"}`, `DEFAULT_TAIL = 150`
  - `@dataclass Section(name: str, start_ts: int, end_ts: int|None, start_line: int, end_line: int)` with property `duration -> int|None`
  - `clean_line(raw: str, *, strip_ansi: bool = True) -> str` — removes ANSI (optional), applies `\r` overwrite semantics
  - `parse_trace(raw: str, *, strip_ansi: bool = True) -> tuple[list[str], list[Section]]`
  - `tail(lines, n) / head(lines, n) -> list[str]` (`n == 0` → all)
  - `grep(lines, pattern, context=0) -> list[str]` — `"<1-based line>: <text>"`, `--` between gaps
  - `sections_summary(sections) -> list[str]`, `section_body(lines, sections, name) -> list[str] | None`
  - `cache_path(cache_dir: Path, job_id: int) -> Path`
  - `fetch_trace(client, project_id: int, job_id: int, cache_dir: Path, *, refresh=False) -> tuple[dict, Path, int]`
  - `header_line(job: dict, size: int) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_log.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
from gitlab_client.log import (
    clean_line,
    fetch_trace,
    grep,
    head,
    header_line,
    parse_trace,
    section_body,
    sections_summary,
    tail,
)

TRACE = (
    "\x1b[0KRunning with gitlab-runner 15.11.0\x1b[0;m\n"
    "section_start:1700000000:prepare_executor\r\x1b[0KPreparing the \"docker\" executor\x1b[0;m\n"
    "Using Docker executor with image python:3.11 ...\n"
    "section_end:1700000004:prepare_executor\r\x1b[0K\n"
    "section_start:1700000004:step_script[collapsed=true]\r\x1b[0K\x1b[32;1m$ pytest -q\x1b[0;m\n"
    "section_start:1700000005:download_deps\r\x1b[0KDownloading  10%\rDownloading  55%\rDownloading 100%\n"
    "section_end:1700000008:download_deps\r\x1b[0K\n"
    "FAILED tests/test_x.py::test_y - AssertionError\n"
    "\x1b[31;1mERROR: Job failed: exit code 1\x1b[0;m\n"
    "section_end:1700000031:step_script\r\x1b[0K\n"
    "section_start:1700000031:cleanup\r\x1b[0K\n"
    "Cleaning up project directory\n"
)
CLEAN = [
    "Running with gitlab-runner 15.11.0",
    'Preparing the "docker" executor',
    "Using Docker executor with image python:3.11 ...",
    "$ pytest -q",
    "Downloading 100%",
    "FAILED tests/test_x.py::test_y - AssertionError",
    "ERROR: Job failed: exit code 1",
    "Cleaning up project directory",
]
JOB = {"id": 5, "name": "test", "stage": "test", "status": "failed", "duration": 31.2, "web_url": "https://g/j/5"}
PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "u"}


def test_clean_line():
    assert clean_line("\x1b[32;1mgreen\x1b[0;m") == "green"
    assert clean_line("a\rb\rc") == "c"
    assert clean_line("keep\r") == "keep"
    assert clean_line("\x1b]0;title\x07text") == "text"
    assert clean_line("\x1b[32;1mgreen\x1b[0;m", strip_ansi=False) == "\x1b[32;1mgreen\x1b[0;m"


def test_parse_trace_sections_and_cleanup():
    lines, sections = parse_trace(TRACE)
    assert lines == CLEAN
    assert [(s.name, s.start_line, s.end_line, s.duration) for s in sections] == [
        ("prepare_executor", 1, 3, 4),
        ("step_script", 3, 7, 27),
        ("download_deps", 4, 5, 3),  # nested inside step_script
        ("cleanup", 7, 8, None),
    ]
    assert sections_summary(sections) == [
        "prepare_executor · 2 lines · 4s",
        "step_script · 4 lines · 27s",
        "download_deps · 1 lines · 3s",
        "cleanup · 1 lines · unterminated",
    ]
    assert section_body(lines, sections, "step_script") == CLEAN[3:7]
    assert section_body(lines, sections, "nope") is None


def test_parse_trace_raw_keeps_ansi():
    lines, sections = parse_trace(TRACE, strip_ansi=False)
    assert "\x1b[32;1m$ pytest -q" in lines[3]
    assert [s.name for s in sections] == ["prepare_executor", "step_script", "download_deps", "cleanup"]


def test_views():
    lines = [f"l{i}" for i in range(10)]
    assert tail(lines, 3) == ["l7", "l8", "l9"] and tail(lines, 0) == lines
    assert head(lines, 2) == ["l0", "l1"] and head(lines, 0) == lines
    assert grep(lines, "L[28]", context=1) == ["2: l1", "3: l2", "4: l3", "--", "8: l7", "9: l8", "10: l9"]
    assert grep(lines, "zzz") == []


def test_fetch_trace_caches_terminal_jobs(client, opener, tmp_path):
    cache = tmp_path / "c"
    opener.add(200, JOB).add(200, b"line1\nline2\n")
    job, path, size = fetch_trace(client, 42, 5, cache)
    assert job == JOB and path == cache / "job-5.log" and size == 12 and path.read_bytes() == b"line1\nline2\n"
    assert opener.requests[0].full_url.endswith("/projects/42/jobs/5")
    assert opener.requests[1].full_url.endswith("/projects/42/jobs/5/trace")
    assert opener.requests[1].get_header("Accept") is None

    opener.add(200, JOB)  # cached: only the job lookup happens
    assert fetch_trace(client, 42, 5, cache)[2] == 12
    assert len(opener.requests) == 3

    opener.add(200, JOB).add(200, b"new\n")
    assert fetch_trace(client, 42, 5, cache, refresh=True)[2] == 4

    opener.add(200, dict(JOB, status="running")).add(200, b"partial\n")
    assert fetch_trace(client, 42, 5, cache)[2] == 8


def test_header_line():
    assert header_line(JOB, 12) == "job 5 test · stage test · failed · 31s · 12 bytes · https://g/j/5"
    assert header_line({"id": 5, "name": "t", "stage": "s", "status": "running", "duration": None, "web_url": "u"}, 0) == "job 5 t · stage s · running · - · 0 bytes · u"


def test_gl_log_cli(run_gl, opener):
    # First call downloads and caches (status=failed is terminal).
    opener.add(200, PROJECT_JSON).add(200, JOB).add(200, TRACE.encode())
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--sections")
    assert code == 0, err
    assert out.splitlines()[0] == f"job 5 test · stage test · failed · 31s · {len(TRACE.encode())} bytes · https://g/j/5"
    assert "step_script · 4 lines · 27s" in out

    opener.add(200, PROJECT_JSON).add(200, JOB)  # served from cache from here on
    code, out, _ = run_gl("log", "5", "--project", "group/proj", "--section", "step_script", "--grep", "failed")
    assert code == 0 and out.splitlines()[1:] == ["3: FAILED tests/test_x.py::test_y - AssertionError", "4: ERROR: Job failed: exit code 1"]

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, _ = run_gl("log", "5", "--project", "group/proj", "--tail", "2")
    assert out.splitlines()[1:] == ["ERROR: Job failed: exit code 1", "Cleaning up project directory"]

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, _ = run_gl("log", "5", "--project", "group/proj")
    assert code == 0 and out.splitlines()[1:] == CLEAN  # fewer than 150 lines → whole log

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--tail", "0")
    assert code == 0 and "printing the whole log" in err

    opener.add(200, PROJECT_JSON).add(200, JOB)
    code, out, err = run_gl("log", "5", "--project", "group/proj", "--section", "nope")
    assert code == 1 and "no section named 'nope'" in err
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_log.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client.log'`

- [ ] **Step 3: Implement log.py**

`src/gitlab_client/log.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""Job log (trace) handling: download to a cache file, strip ANSI, parse runner sections,
and expose bounded views. The full log is never returned to the caller as one blob.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .http import Client

# CSI sequences (colours, erase-line, cursor moves) and OSC sequences (titles).
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# section_start:<unix ts>:<name>[collapsed=true]  /  section_end:<unix ts>:<name>
SECTION_RE = re.compile(r"section_(start|end):(\d+):([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?")
TERMINAL = {"success", "failed", "canceled", "skipped"}
DEFAULT_TAIL = 150


@dataclass
class Section:
    name: str
    start_ts: int
    end_ts: int | None
    start_line: int  # index into the cleaned lines (first body line)
    end_line: int  # exclusive

    @property
    def duration(self) -> int | None:
        return None if self.end_ts is None else self.end_ts - self.start_ts


def clean_line(raw: str, *, strip_ansi: bool = True) -> str:
    text = ANSI_RE.sub("", raw) if strip_ansi else raw
    if "\r" in text:
        segments = [s for s in text.split("\r") if s]  # terminal overwrite: last non-empty wins
        text = segments[-1] if segments else ""
    return text.rstrip("\n")


def parse_trace(raw: str, *, strip_ansi: bool = True) -> tuple[list[str], list[Section]]:
    lines: list[str] = []
    sections: list[Section] = []
    open_sections: dict[str, Section] = {}
    # Split on "\n" only: str.splitlines() would also split on the bare "\r" that clean_line must see.
    raw_lines = raw.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()  # a trailing newline, not an empty last line
    for raw_line in raw_lines:
        for m in SECTION_RE.finditer(raw_line):
            kind, ts, name = m.group(1), int(m.group(2)), m.group(3)
            if kind == "start":
                sec = Section(name, ts, None, len(lines), len(lines))
                open_sections[name] = sec
                sections.append(sec)
            elif name in open_sections:
                sec = open_sections.pop(name)
                sec.end_ts, sec.end_line = ts, len(lines)
        stripped = SECTION_RE.sub("", raw_line)
        cleaned = clean_line(stripped, strip_ansi=strip_ansi)
        if stripped != raw_line and not clean_line(stripped).strip():
            continue  # the line held only markers
        lines.append(cleaned)
    for sec in open_sections.values():
        sec.end_line = len(lines)
    return lines, sections


def tail(lines: list[str], n: int) -> list[str]:
    return lines[-n:] if n else lines


def head(lines: list[str], n: int) -> list[str]:
    return lines[:n] if n else lines


def grep(lines: list[str], pattern: str, context: int = 0) -> list[str]:
    rx = re.compile(pattern, re.IGNORECASE)
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if rx.search(line):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    out: list[str] = []
    prev: int | None = None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("--")
        out.append(f"{i + 1}: {lines[i]}")
        prev = i
    return out


def sections_summary(sections: list[Section]) -> list[str]:
    return [
        f"{s.name} · {s.end_line - s.start_line} lines · "
        + (f"{s.duration}s" if s.duration is not None else "unterminated")
        for s in sections
    ]


def section_body(lines: list[str], sections: list[Section], name: str) -> list[str] | None:
    for s in sections:
        if s.name == name:
            return lines[s.start_line : s.end_line]
    return None


def cache_path(cache_dir: Path, job_id: int) -> Path:
    return cache_dir / f"job-{job_id}.log"


def fetch_trace(client: Client, project_id: int, job_id: int, cache_dir: Path, *, refresh: bool = False) -> tuple[dict, Path, int]:
    """Return (job json, cached log path, size). Terminal jobs are cached; others re-download."""
    job = client.request("GET", f"/projects/{project_id}/jobs/{job_id}").json() or {}
    path = cache_path(cache_dir, job_id)
    if refresh or job.get("status") not in TERMINAL or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            client.request("GET", f"/projects/{project_id}/jobs/{job_id}/trace", accept_json=False, stream_to=fh)
    return job, path, path.stat().st_size


def header_line(job: dict[str, Any], size: int) -> str:
    duration = job.get("duration")
    dur = f"{duration:.0f}s" if isinstance(duration, (int, float)) else "-"
    return f"job {job.get('id')} {job.get('name')} · stage {job.get('stage')} · {job.get('status')} · {dur} · {size} bytes · {job.get('web_url')}"
```

- [ ] **Step 4: Add `cmd_log` to cli.py**

In `src/gitlab_client/cli.py`, add `from . import log` to the imports, add this function after `cmd_version`, and change `"log": _not_implemented` to `"log": cmd_log` in `COMMANDS`:

```python
def cmd_log(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    job, path, size = log.fetch_trace(ctx.client, ctx.project.id, args.job_id, ctx.cache_dir, refresh=args.refresh)
    out.write(log.header_line(job, size) + "\n")
    raw = path.read_bytes().decode("utf-8", errors="replace")  # bytes: text mode would turn bare \r into newlines
    lines, sections = log.parse_trace(raw, strip_ansi=not args.raw)
    if args.sections:
        out.write("".join(s + "\n" for s in log.sections_summary(sections)))
        return 0
    if args.section:
        body = log.section_body(lines, sections, args.section)
        if body is None:
            raise GlError(f"no section named {args.section!r} (use --sections to list them)")
        lines = body
    if args.grep:
        out.write("".join(s + "\n" for s in log.grep(lines, args.grep, args.context)))
        return 0
    if args.head is not None:
        lines = log.head(lines, args.head)
    elif args.tail is not None:
        if args.tail == 0:
            ctx.warn(f"gl: warning: printing the whole log ({len(lines)} lines, {size} bytes)")
        lines = log.tail(lines, args.tail)
    elif not args.section:
        lines = log.tail(lines, log.DEFAULT_TAIL)
    out.write("".join(s + "\n" for s in lines))
    return 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass (`test_log.py`: 7 passed)

- [ ] **Step 6: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — gl log: cached trace download, ANSI/section parsing, tail/head/grep/section views"
```

---

### Task 8: diff.py + `gl diff` — MR / commit / compare as unified diff

**Files:**
- Create: `src/gitlab_client/diff.py`
- Modify: `src/gitlab_client/cli.py` (add `cmd_diff`, register)
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: `http.Client.{request, paginate}`, `http.encode_path_segment`.
- Produces (`gitlab_client.diff`):
  - `OMITTED = "[diff omitted by server — exceeds the instance's diff limits]"`
  - `file_status(d: dict) -> str` (`A`/`M`/`D`/`R`)
  - `render_file(d: dict) -> str`, `render_diffs(diffs: list[dict]) -> str`, `render_files(diffs) -> str`, `render_compare(payload: dict) -> str`
  - `mr_diffs(client, project_id, iid, warn) -> list[dict]`, `commit_diff(client, project_id, sha) -> list[dict]`, `compare(client, project_id, frm, to, straight=False) -> dict`

- [ ] **Step 1: Write the failing tests**

`tests/test_diff.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
from gitlab_client.diff import OMITTED, file_status, render_compare, render_file, render_files

FLAGS = {"new_file": False, "renamed_file": False, "deleted_file": False}
NEW = {**FLAGS, "old_path": "a.py", "new_path": "a.py", "a_mode": "0", "b_mode": "100644", "diff": "@@ -0,0 +1 @@\n+print(1)\n", "new_file": True}
MOD = {**FLAGS, "old_path": "m.py", "new_path": "m.py", "a_mode": "100644", "b_mode": "100644", "diff": "@@ -1 +1 @@\n-x\n+y\n"}
DEL = {**FLAGS, "old_path": "d.py", "new_path": "d.py", "a_mode": "100644", "b_mode": "0", "diff": "@@ -1 +0,0 @@\n-gone\n", "deleted_file": True}
REN = {**FLAGS, "old_path": "old.txt", "new_path": "new.txt", "a_mode": "100644", "b_mode": "100644", "diff": "", "renamed_file": True}
BIG = {**FLAGS, "old_path": "big.bin", "new_path": "big.bin", "a_mode": "100644", "b_mode": "100644", "diff": ""}
PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "u"}


def test_file_status():
    assert [file_status(d) for d in (NEW, MOD, DEL, REN, BIG)] == ["A", "M", "D", "R", "M"]


def test_render_file_variants():
    assert render_file(NEW) == "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+print(1)\n"
    assert render_file(MOD) == "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert render_file(DEL) == "diff --git a/d.py b/d.py\ndeleted file mode 100644\n--- a/d.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
    assert render_file(REN) == "diff --git a/old.txt b/new.txt\nrename from old.txt\nrename to new.txt\n--- a/old.txt\n+++ b/new.txt\n"
    assert render_file(BIG) == f"diff --git a/big.bin b/big.bin\n--- a/big.bin\n+++ b/big.bin\n{OMITTED}\n"
    empty_new = {**NEW, "diff": ""}  # e.g. an empty file added — not a truncation
    assert render_file(empty_new) == "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"


def test_render_files():
    assert render_files([NEW, MOD, DEL, REN]) == "A a.py\nM m.py\nD d.py\nR new.txt (from old.txt)\n4 file(s)\n"


def test_render_compare():
    payload = {"commits": [{"short_id": "abc1234", "title": "Fix it"}], "diffs": [MOD], "compare_timeout": False}
    assert render_compare(payload) == "abc1234 Fix it\n\n" + render_file(MOD)
    assert render_compare({"commits": [], "diffs": [], "compare_timeout": True}) == "[compare timed out on the server — result may be partial]\n"


def test_gl_diff_cli(run_gl, opener):
    opener.add(200, PROJECT_JSON).add(200, [NEW, MOD], {"X-Next-Page": ""})
    code, out, err = run_gl("diff", "12", "--project", "group/proj")
    assert code == 0, err
    assert out == render_file(NEW) + render_file(MOD)
    assert opener.last.full_url.endswith("/projects/42/merge_requests/12/diffs?per_page=100&page=1")

    opener.add(200, PROJECT_JSON).add(200, [NEW, MOD], {"X-Next-Page": ""})
    code, out, _ = run_gl("diff", "12", "--project", "group/proj", "--files")
    assert out == "A a.py\nM m.py\n2 file(s)\n"

    opener.add(200, PROJECT_JSON).add(200, [NEW, MOD], {"X-Next-Page": ""})
    code, out, _ = run_gl("diff", "12", "--project", "group/proj", "--file", "m.py")
    assert out == render_file(MOD)

    opener.add(200, PROJECT_JSON).add(200, [NEW], {"X-Next-Page": ""})
    code, out, err = run_gl("diff", "12", "--project", "group/proj", "--file", "zzz")
    assert code == 1 and "no file 'zzz'" in err

    opener.add(200, PROJECT_JSON).add(200, [MOD])
    code, out, _ = run_gl("diff", "--commit", "abc123", "--project", "group/proj")
    assert code == 0 and out == render_file(MOD)
    assert opener.last.full_url.endswith("/projects/42/repository/commits/abc123/diff")

    opener.add(200, PROJECT_JSON).add(200, {"commits": [{"short_id": "abc1234", "title": "T"}], "diffs": [MOD], "compare_timeout": False})
    code, out, _ = run_gl("diff", "--range", "v1.0..release/2.0", "--straight", "--project", "group/proj")
    assert code == 0 and out.startswith("abc1234 T\n\n")
    assert opener.last.full_url.endswith("/repository/compare?from=v1.0&to=release%2F2.0&straight=true")

    code, _, err = run_gl("diff", "--range", "nodots", "--project", "group/proj")
    assert code == 2 and "FROM..TO" in err
    code, _, err = run_gl("diff", "--project", "group/proj")
    assert code == 2 and "MR_IID" in err
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_diff.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client.diff'`

- [ ] **Step 3: Implement diff.py**

`src/gitlab_client/diff.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""Render GitLab's per-file diff JSON (MR diffs, commit diff, compare) as unified diff text.

GitLab already supplies the `@@` hunks in `diff`; we add the git-style file headers. An
empty `diff` on a file that is neither new, deleted, nor renamed means the server omitted it
because of the instance diff limits (15.11 has no collapsed/too_large flags). A renamed-and-modified
file over the limit therefore renders as a pure rename — a known simplification.
"""
from __future__ import annotations

from typing import Any, Callable

from .http import Client, encode_path_segment

OMITTED = "[diff omitted by server — exceeds the instance's diff limits]"


def file_status(d: dict[str, Any]) -> str:
    if d.get("new_file"):
        return "A"
    if d.get("deleted_file"):
        return "D"
    if d.get("renamed_file"):
        return "R"
    return "M"


def render_file(d: dict[str, Any]) -> str:
    old, new = d.get("old_path") or "", d.get("new_path") or ""
    lines = [f"diff --git a/{old} b/{new}"]
    if d.get("new_file"):
        lines.append(f"new file mode {d.get('b_mode') or '100644'}")
    if d.get("deleted_file"):
        lines.append(f"deleted file mode {d.get('a_mode') or '100644'}")
    if d.get("renamed_file"):
        lines += [f"rename from {old}", f"rename to {new}"]
    lines.append("--- /dev/null" if d.get("new_file") else f"--- a/{old}")
    lines.append("+++ /dev/null" if d.get("deleted_file") else f"+++ b/{new}")
    body = (d.get("diff") or "").rstrip("\n")
    if body:
        lines.append(body)
    elif not (d.get("new_file") or d.get("deleted_file") or d.get("renamed_file")):
        lines.append(OMITTED)  # an empty diff on a plain modification = server hit the diff limits
    return "\n".join(lines) + "\n"


def render_diffs(diffs: list[dict[str, Any]]) -> str:
    return "".join(render_file(d) for d in diffs)


def render_files(diffs: list[dict[str, Any]]) -> str:
    rows = []
    for d in diffs:
        row = f"{file_status(d)} {d.get('new_path') or d.get('old_path')}"
        if d.get("renamed_file"):
            row += f" (from {d.get('old_path')})"
        rows.append(row)
    rows.append(f"{len(diffs)} file(s)")
    return "\n".join(rows) + "\n"


def render_compare(payload: dict[str, Any]) -> str:
    commits = payload.get("commits") or []
    out = "".join(f"{c.get('short_id') or str(c.get('id', ''))[:8]} {c.get('title', '')}\n" for c in commits)
    if out:
        out += "\n"
    if payload.get("compare_timeout"):
        out += "[compare timed out on the server — result may be partial]\n"
    return out + render_diffs(payload.get("diffs") or [])


def mr_diffs(client: Client, project_id: int, iid: int, warn: Callable[[str], None]) -> list[dict[str, Any]]:
    return client.paginate(f"/projects/{project_id}/merge_requests/{iid}/diffs", None, max_items=3000, warn=warn)


def commit_diff(client: Client, project_id: int, sha: str) -> list[dict[str, Any]]:
    return client.request("GET", f"/projects/{project_id}/repository/commits/{encode_path_segment(sha)}/diff").json() or []


def compare(client: Client, project_id: int, frm: str, to: str, straight: bool = False) -> dict[str, Any]:
    return client.request("GET", f"/projects/{project_id}/repository/compare", query={"from": frm, "to": to, "straight": straight}).json() or {}
```

- [ ] **Step 4: Add `cmd_diff` to cli.py**

Change the helper import to `from . import diff, log`, add after `cmd_log`, and register `"diff": cmd_diff`:

```python
def cmd_diff(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    if not (args.range or args.commit or args.mr_iid is not None):
        raise ConfigError("give MR_IID, --commit SHA, or --range FROM..TO")
    if args.range:
        frm, sep, to = args.range.partition("..")
        if not sep or not frm or not to:
            raise ConfigError("--range needs FROM..TO")
    pid = ctx.project.id  # resolved only after argument validation, so usage errors never hit the network
    if args.range:
        payload = diff.compare(ctx.client, pid, frm, to, args.straight)
        diffs = payload.get("diffs") or []
        if not args.files and not args.file:
            out.write(diff.render_compare(payload))
            return 0
    elif args.commit:
        diffs = diff.commit_diff(ctx.client, pid, args.commit)
    else:
        diffs = diff.mr_diffs(ctx.client, pid, args.mr_iid, ctx.warn)
    if args.file:
        diffs = [d for d in diffs if args.file in (d.get("new_path"), d.get("old_path"))]
        if not diffs:
            raise GlError(f"no file {args.file!r} in this diff")
    out.write(diff.render_files(diffs) if args.files else diff.render_diffs(diffs))
    return 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass (`test_diff.py`: 5 passed)

- [ ] **Step 6: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — gl diff: MR / commit / compare rendered as unified diff, --files and --file"
```

---

### Task 9: artifacts.py + `gl artifacts` — archive download/list/extract, single file, by-ref

**Files:**
- Create: `src/gitlab_client/artifacts.py`
- Modify: `src/gitlab_client/cli.py` (add `cmd_artifacts`, register; delete `_not_implemented`)
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: `http.Client.request` (`stream_to`), `http.encode_path_segment`, `errors.{GlError, HttpError}`.
- Produces (`gitlab_client.artifacts`):
  - `BY_REF_HINT: str`
  - `encode_artifact_path(p: str) -> str`
  - `cache_path(cache_dir: Path, job_id: int) -> Path`
  - `download_archive(client, project_id, dest: Path, *, job_id=None, ref=None, job=None) -> int`
  - `fetch_file(client, project_id, artifact_path: str, *, job_id=None, ref=None, job=None) -> bytes`
  - `list_archive(path: Path) -> list[tuple[int, str]]`
  - `extract_archive(path: Path, dest: Path) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_artifacts.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
import io
import zipfile

import pytest

from gitlab_client.artifacts import (
    BY_REF_HINT,
    download_archive,
    encode_artifact_path,
    extract_archive,
    fetch_file,
    list_archive,
)
from gitlab_client.errors import GlError, HttpError

PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "u"}


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


GOOD = {"reports/junit.xml": b"<testsuite/>", "dist/app.tar": b"x" * 10, "dist/": b""}
EVIL = {"ok.txt": b"fine", "../evil.txt": b"nope"}


def test_encode_artifact_path():
    assert encode_artifact_path("reports/a b.xml") == "reports/a%20b.xml"
    assert encode_artifact_path("/dir/x#1.txt/") == "dir/x%231.txt"


def test_list_and_extract(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip(GOOD))
    assert list_archive(archive) == [(12, "reports/junit.xml"), (10, "dist/app.tar")]
    dest = tmp_path / "out"
    assert extract_archive(archive, dest) == 2
    assert (dest / "reports/junit.xml").read_bytes() == b"<testsuite/>" and (dest / "dist").is_dir()


def test_extract_refuses_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(make_zip(EVIL))
    with pytest.raises(GlError, match="refusing to extract '../evil.txt'"):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_download_archive_by_job_and_by_ref(client, opener, tmp_path):
    opener.add(200, b"zipbytes")
    dest = tmp_path / "c" / "job-5-artifacts.zip"
    assert download_archive(client, 42, dest, job_id=5) == 8 and dest.read_bytes() == b"zipbytes"
    assert opener.last.full_url.endswith("/projects/42/jobs/5/artifacts") and opener.last.get_header("Accept") is None

    opener.add(200, b"zip2")
    assert download_archive(client, 42, dest, ref="release/1.0", job="build") == 4
    assert opener.last.full_url.endswith("/projects/42/jobs/artifacts/release%2F1.0/download?job=build")

    opener.add(404, {"message": "404 Not Found"})
    with pytest.raises(HttpError) as e:
        download_archive(client, 42, dest, ref="main", job="build")
    assert BY_REF_HINT in str(e.value) and not dest.exists()

    opener.add(404, {"message": "404 Not Found"})
    with pytest.raises(HttpError) as e:
        download_archive(client, 42, dest, job_id=5)
    assert BY_REF_HINT not in str(e.value)


def test_fetch_file(client, opener):
    opener.add(200, b"<testsuite/>")
    assert fetch_file(client, 42, "reports/junit.xml", job_id=5) == b"<testsuite/>"
    assert opener.last.full_url.endswith("/projects/42/jobs/5/artifacts/reports/junit.xml")
    opener.add(200, b"y")
    assert fetch_file(client, 42, "a b.txt", ref="main", job="build") == b"y"
    assert opener.last.full_url.endswith("/projects/42/jobs/artifacts/main/raw/a%20b.txt?job=build")


def test_gl_artifacts_cli(run_gl, opener, tmp_path):
    opener.add(200, PROJECT_JSON).add(200, make_zip(GOOD))
    code, out, err = run_gl("artifacts", "5", "--project", "group/proj", "--list")
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].endswith("job-5-artifacts.zip (" + str(len(make_zip(GOOD))) + " bytes)")
    assert lines[1:] == ["        12 reports/junit.xml", "        10 dist/app.tar"]

    opener.add(200, PROJECT_JSON)  # cached by job id: no download
    dest = tmp_path / "x"
    code, out, _ = run_gl("artifacts", "5", "--project", "group/proj", "--extract", str(dest))
    assert code == 0 and f"extracted 2 file(s) to {dest}" in out and (dest / "reports/junit.xml").exists()

    opener.add(200, PROJECT_JSON).add(200, b"<testsuite/>")
    code, out, _ = run_gl("artifacts", "5", "--project", "group/proj", "--file", "reports/junit.xml")
    assert code == 0 and out == "<testsuite/>"

    opener.add(200, PROJECT_JSON).add(200, make_zip(GOOD))
    code, out, _ = run_gl("artifacts", "--ref", "main", "--job", "build", "--project", "group/proj", "--list")
    assert code == 0 and "reports/junit.xml" in out
    opener.add(200, PROJECT_JSON).add(200, make_zip(GOOD))  # by-ref is never cached
    code, out, _ = run_gl("artifacts", "--ref", "main", "--job", "build", "--project", "group/proj", "--list")
    assert code == 0 and not opener.queue

    code, _, err = run_gl("artifacts", "--project", "group/proj")
    assert code == 2 and "JOB_ID" in err
    code, _, err = run_gl("artifacts", "--ref", "main", "--project", "group/proj")
    assert code == 2 and "--job" in err
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_artifacts.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitlab_client.artifacts'`

- [ ] **Step 3: Implement artifacts.py**

`src/gitlab_client/artifacts.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""Job artifacts: archive download/list/extract and single-file fetch, by job id or by ref+job.

15.11 has no endpoint that lists an archive's contents, so listing means downloading the zip.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .errors import GlError, HttpError
from .http import Client, encode_path_segment

BY_REF_HINT = (
    " (on GitLab 15.x the by-ref route only looks at the single latest *successful* pipeline of "
    "that branch/tag, and SHAs are not accepted — use a job id instead)"
)


def encode_artifact_path(p: str) -> str:
    return "/".join(encode_path_segment(seg) for seg in p.strip("/").split("/") if seg)


def cache_path(cache_dir: Path, job_id: int) -> Path:
    return cache_dir / f"job-{job_id}-artifacts.zip"


def _route(project_id: int, *, job_id: int | None, ref: str | None, job: str | None, by_job: str, by_ref: str) -> tuple[str, dict | None]:
    if job_id is not None:
        return f"/projects/{project_id}/jobs/{job_id}/artifacts{by_job}", None
    if not ref or not job:
        raise GlError("artifacts need a job id, or --ref REF --job NAME")
    return f"/projects/{project_id}/jobs/artifacts/{encode_path_segment(ref)}{by_ref}", {"job": job}


def _with_hint(e: HttpError, job_id: int | None) -> HttpError:
    if e.status == 404 and job_id is None:
        return HttpError(e.status, e.method, e.url, e.detail + BY_REF_HINT)
    return e


def download_archive(client: Client, project_id: int, dest: Path, *, job_id: int | None = None, ref: str | None = None, job: str | None = None) -> int:
    path, query = _route(project_id, job_id=job_id, ref=ref, job=job, by_job="", by_ref="/download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "wb") as fh:
            resp = client.request("GET", path, query=query, accept_json=False, stream_to=fh)
    except HttpError as e:
        dest.unlink(missing_ok=True)
        raise _with_hint(e, job_id) from None
    return resp.bytes_written


def fetch_file(client: Client, project_id: int, artifact_path: str, *, job_id: int | None = None, ref: str | None = None, job: str | None = None) -> bytes:
    enc = "/" + encode_artifact_path(artifact_path)
    path, query = _route(project_id, job_id=job_id, ref=ref, job=job, by_job=enc, by_ref="/raw" + enc)
    try:
        return client.request("GET", path, query=query, accept_json=False).body
    except HttpError as e:
        raise _with_hint(e, job_id) from None


def list_archive(path: Path) -> list[tuple[int, str]]:
    with zipfile.ZipFile(path) as zf:
        return [(info.file_size, info.filename) for info in zf.infolist() if not info.is_dir()]


def extract_archive(path: Path, dest: Path) -> int:
    """Extract into dest, refusing any entry that would land outside it. Returns the file count."""
    dest = dest.resolve()
    count = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise GlError(f"refusing to extract {info.filename!r}: it escapes {dest}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count
```

- [ ] **Step 4: Add `cmd_artifacts` to cli.py**

Change the helper import to `from . import artifacts, diff, log`, add after `cmd_diff`, register `"artifacts": cmd_artifacts`, and delete `_not_implemented`:

```python
def cmd_artifacts(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    if (args.job_id is None) == (args.ref is None):
        raise ConfigError("give JOB_ID, or --ref REF --job NAME")
    if args.ref and not args.job:
        raise ConfigError("--ref needs --job NAME")
    pid = ctx.project.id
    selector: dict[str, Any] = {"job_id": args.job_id} if args.job_id is not None else {"ref": args.ref, "job": args.job}

    if args.file:
        data = artifacts.fetch_file(ctx.client, pid, args.file, **selector)
        if args.out:
            Path(args.out).write_bytes(data)
            out.write(f"wrote {len(data)} bytes to {args.out}\n")
        else:
            out.write(data.decode("utf-8", errors="replace"))
        return 0

    if args.job_id is not None:
        dest = artifacts.cache_path(ctx.cache_dir, args.job_id)
        if args.refresh or not dest.is_file():
            size = artifacts.download_archive(ctx.client, pid, dest, **selector)
        else:
            size = dest.stat().st_size
    else:  # by ref: "latest successful" moves, so never cache
        dest = ctx.cache_dir / f"ref-{args.ref.replace('/', '%2F')}-{args.job}-artifacts.zip"
        size = artifacts.download_archive(ctx.client, pid, dest, **selector)
    out.write(f"{dest} ({size} bytes)\n")
    if args.list:
        out.write("".join(f"{s:>10} {name}\n" for s, name in artifacts.list_archive(dest)))
    if args.extract:
        n = artifacts.extract_archive(dest, Path(args.extract))
        out.write(f"extracted {n} file(s) to {args.extract}\n")
    return 0
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass (`test_artifacts.py`: 6 passed)

- [ ] **Step 6: Commit**

```bash
git add plugins/gitlab-client/scripts/gitlab-client
git commit -m "gitlab-client — gl artifacts: cached archive download, safe extract, single-file and by-ref routes"
```

---

### Task 10: Skill `gitlab-client` — SKILL.md and the six reference docs

**Files:**
- Create: `plugins/gitlab-client/skills/gitlab-client/SKILL.md`
- Create: `plugins/gitlab-client/skills/gitlab-client/references/{local-settings,repo-browsing,merge-requests,pipelines,safety-perimeter,v15-compat}.md`

**Interfaces:**
- Consumes: the `gl` surface exactly as built in Tasks 6–9 (verbs, flags, exit codes, error wording). Every command shown in these docs must run as written against the parser in `cli.py` — check `gl <verb> --help` while writing.
- Produces: the skill Claude loads; the agent (Task 11) and commands (Task 12) refer to these files by path.

Docs are prose, not code, so this task has no test step; the gate is `claude plugin validate plugins/gitlab-client` (frontmatter well-formed) plus a read-through against spec §5 and Appendix A. Write reference-style (purpose → command → gotcha), no tutorials.

- [ ] **Step 1: Write SKILL.md**

`skills/gitlab-client/SKILL.md`:

````markdown
---
name: gitlab-client
description: Work with a self-managed GitLab (15.x) through the bundled `gl` CLI. Activate when the user mentions GitLab, a merge request / MR, a pipeline, a CI job, a job log, an artifact, "what's failing on my branch", pastes a GitLab URL, names a `group/project`, asks to open or update an MR, reply to or resolve a review thread, retry or cancel a job, trigger a pipeline, or read a file / commit / diff from a GitLab project that isn't cloned locally. Reads freely; writes are allow-listed (create/update MRs, comments, retry/cancel/play/trigger) and MR/comment writes are confirmed first; merge, approve, delete, and settings changes are refused. Not for GitHub, Bitbucket, or GitLab issues/epics.
---

# gitlab-client

Make Claude an effective, safe user of an on-prem GitLab through `gl`, a small bundled REST v4 client. The skill owns *which* calls to make and *when to stop and ask*; `gl` owns auth, encoding, pagination, and the write allow-list. The reference docs are written for **GitLab 15.11** — `gl version` warns when the instance is something else.

`gl` lives at `../../scripts/gl` relative to this skill's directory (`${CLAUDE_PLUGIN_ROOT}/scripts/gl`). Resolve it to an absolute path once per turn and use that. It needs only `python3` ≥ 3.10.

## Discovery (once per turn, before anything else)

```bash
gl version
```

- **Exit 0** → JSON with `version`, `enterprise`, `url`. Proceed. A stderr warning about the version means the references may be slightly off — say so if something 404s.
- **Exit 2, "no GitLab URL configured" / "no token_cmd configured"** → the user hasn't run `/gitlab-client:setup`. Say so and stop; don't ask for a token in chat.
- **Exit 2, "token_cmd exited N"** → their secret-store command failed; show the stderr `gl` printed (it never contains the token) and stop.
- **Exit 1** → network/HTTP problem; the message names the host and the status. Suggest `/gitlab-client:doctor`.

Never read `~/.claude/gitlab-client.local.md` or `.claude/gitlab-client.local.md` yourself and never run `token_cmd` — `gl` does both internally so the token never enters the transcript.

## Which project?

`gl` resolves the project in this order: `--project group/name` (or a pasted GitLab URL) → `project:` in `.claude/gitlab-client.local.md` → the `origin` remote of the current repo (`--remote NAME` for another remote). `gl project` shows the result. In `gl api` paths write `:project` and it expands to the URL-encoded path:

```bash
gl api GET /projects/:project/merge_requests state=opened --fields iid,title,author.username,web_url
gl api GET /projects/:project/merge_requests --project other-group/other-repo --fields iid,title
```

A 404 on project resolution says *not found or not visible*; ask the user for the path rather than guessing.

## Command cheat-sheet

```bash
gl api GET|POST|PUT PATH [k=v | k:=json | k[]=v ...] [--all] [--max N] [--fields a,b.c] [--out FILE] [--json BODY]
gl project                      # {id, path_with_namespace, default_branch, web_url, resolved_from: flag|settings|remote:<name>}
gl log JOB_ID [--tail N|--head N|--grep RE [-C N]|--sections|--section NAME] [--raw] [--refresh]
gl diff MR_IID [--files] [--file PATH] · gl diff --commit SHA · gl diff --range A..B [--straight]
gl artifacts JOB_ID [--list|--extract DIR|--file PATH [--out FILE]] · gl artifacts --ref REF --job NAME …
```

Params: `key=value` is a string, `key:=<json>` is typed (`squash:=true`, `reviewer_ids:=[12]`, `variables:='[{"key":"ENV","value":"staging"}]'`), `key[]=v` repeats. GET params become the query string; POST/PUT params become a JSON body. Exit codes: 0 ok · 1 HTTP/network · 2 config/usage · 3 refused by the write policy.

## Write policy (non-negotiable)

`gl` refuses everything not on its allow-list with exit 3; these rules sit on top of that.

| Class | What | Rule |
|---|---|---|
| **Refused** | merge, approve/unapprove, rebase, delete anything, erase job logs, repository writes (branches/tags/files — use git locally), project/group/member/variable/hook/protected-branch settings, users, tokens, `sudo` | Name the operation and hand it back to the user. Don't work around it with curl. |
| **Confirm first** | create MR, update MR (title/description/labels/reviewers/close/reopen), post a note, start a discussion, reply to a thread, resolve a thread | Show `project!iid`, the exact body/title text verbatim, and the command; send only after an explicit yes. One confirmation per distinct payload, even when the user said "just do it". |
| **Go and report** | retry/cancel a job or pipeline, play a manual job, trigger a pipeline (echo the ref + variables), keep artifacts | Run it, then report the new job/pipeline id, status, and `web_url`. |

Why the split: the confirm class is visible to colleagues and hard to unsay; the go class is cheap and reversible. Details and the error semantics (404 ambiguity, 403 role vs scope): [`references/safety-perimeter.md`](references/safety-perimeter.md).

## Context economy

- Lists: always `--fields`; a bare MR or pipeline object is ~2 KB. `--all` only with a filter (`state=opened`, `ref=`, `updated_after=`, `source_branch=`).
- Job logs: never `gl log … --tail 0` for a log you haven't sized; start with `--sections`, then `--section step_script --tail 80` or `--grep 'error|failed' -C 3`.
- Diffs: `gl diff N --files` first; then `--file PATH` for the files that matter.
- Raw files: unknown size → `gl api GET /projects/:project/repository/files/<path%2Fencoded> ref=<ref> --fields size,encoding` first; large → `…/raw ref=<ref> --out /tmp/x` and read it with `Read` (see repo-browsing).

## Inline or dispatch?

- One lookup, one write, one file → inline with `gl`.
- "Why is CI red", failed-pipeline triage, anything that reads logs from more than one job or needs 5+ calls → dispatch [`pipeline-debugger`](../../agents/pipeline-debugger.md). Give it the absolute `gl` path, the project path, the identifiers you have (pipeline id / job id / branch / MR iid), and the question. It returns a short report and never writes.

## References — load the one for the current step

- [`references/local-settings.md`](references/local-settings.md) — the two settings files, `token_cmd` examples, precedence, env overrides.
- [`references/repo-browsing.md`](references/repo-browsing.md) — tree (keyset), files (`ref` required), commits, compare, branches/tags, code search feature-detect.
- [`references/merge-requests.md`](references/merge-requests.md) — list/read, diffs, discussions vs notes, create (`Draft:` prefix), update (`add_labels`, `reviewer_ids`), "the MR for my branch".
- [`references/pipelines.md`](references/pipelines.md) — pipelines/jobs/bridges, logs, retry/cancel/play, trigger, test reports, artifacts.
- [`references/safety-perimeter.md`](references/safety-perimeter.md) — allow-list, confirmation format, error semantics.
- [`references/v15-compat.md`](references/v15-compat.md) — params/routes that don't exist on 15.11 and the deprecated-but-present fields to prefer.

## When to step out

- Issues, epics, wiki, snippets, releases — not covered; say so.
- Merging, approving, rebasing, deleting — refused; the user does it in the GitLab UI or with git.
- GitHub / Bitbucket / gitlab.com SaaS questions — wrong skill (the client would work against gitlab.com, but the references target self-managed 15.11).
- `python-gitlab` / `glab` / CI YAML authoring — not this skill's job.
````

- [ ] **Step 2: Write references/local-settings.md**

Contents (write it out in full — these are the sections and the facts each must carry):

1. **Files and precedence** — `~/.claude/gitlab-client.local.md` (user) and `<repo>/.claude/gitlab-client.local.md` (project); keys `url`, `token_cmd`, `project`; precedence flag → env (`GITLAB_CLIENT_URL`, `GITLAB_CLIENT_TOKEN`) → project file → user file; unknown keys warn and are ignored; edits apply on the next `gl` call (no restart).
2. **Minimum user file** — a fenced example:
   ```markdown
   ---
   # Generated by /gitlab-client:setup. Edits take effect on the next gl call.
   url: https://gitlab.example.com
   token_cmd: ksm secret notation keeper://abc123/field/password
   ---
   ```
3. **`token_cmd` examples** — one line each, recorded verbatim (no invented env placeholders): Keeper `ksm secret notation keeper://<uid>/field/password`; `pass show work/gitlab-pat`; 1Password `op read "op://Work/GitLab PAT/credential"`; env var `printf '%s' "$GITLAB_TOKEN"`. Multi-line via the `token_cmd: |` block scalar. Rules: exit 0, exactly one non-empty stdout line, stdout is never shown on failure but stderr is. **Don't put the token itself in the file** (`echo glpat-…`): `/setup` and `/doctor` may show the `token_cmd` line, and a literal would then land in the transcript — use the env-var form instead.
4. **Project file** — when to add one (`origin` isn't the GitLab remote, relative-URL install, a repo that maps to a different project path); example with `project: platform/backend/api`; must be gitignored (`.claude/*.local.md`).
5. **Token scopes** — `read_api` for everything read-only (including logs and artifacts); `api` for any write; `read_repository` alone is *not* enough. `/gitlab-client:doctor` shows the scopes and expiry; tokens without an expiry get one forced at 16.0.

- [ ] **Step 3: Write references/repo-browsing.md**

Sections, each with the exact `gl` command and the 15.11 gotcha (source: spec Appendix A.2):

1. **Project** — `gl project`; `gl api GET /projects/:project --fields id,path_with_namespace,default_branch,web_url,permissions`.
2. **Tree** — `gl api GET /projects/:project/repository/tree path=src ref=main recursive:=true --all --fields path,type`; keyset paging is automatic for this path; a missing path returns `[]` not 404.
3. **Files** — `ref` is required on 15.11. JSON metadata: `gl api GET /projects/:project/repository/files/src%2Fapp.py ref=main --fields size,encoding,blob_id,last_commit_id` (encode the *whole* path with `%2F`). Raw: `… /repository/files/src%2Fapp.py/raw ref=main` — to a file with `--out` when large; by blob: `/repository/blobs/<sha>/raw`. Blame: `…/files/<path>/blame ref=main range[start]=1 range[end]=80`.
4. **Commits** — list `…/repository/commits ref_name=main path=src since=2026-08-01T00:00:00Z --fields short_id,title,author_name,created_at`; `ref_name=v1.0..main` ranges; single `…/commits/<sha> --fields id,title,last_pipeline.status,stats`; diff via `gl diff --commit <sha>`; `…/commits/<sha>/refs`, `…/commits/<sha>/merge_requests`, `…/commits/<sha>/statuses`.
5. **Compare** — `gl diff --range main..feature/x` (merge-base semantics; `--straight` for two-dot); unpaginated; `compare_timeout`.
6. **Branches / tags** — `…/repository/branches search=^feature --all --fields name,commit.short_id,merged,protected`; `…/repository/tags order_by=version --fields name,commit.short_id,message`; `/`-containing names `%2F`-encoded when in the path.
7. **Code search** — `gl api GET /projects/:project/search scope=blobs search="def resolve_project" --fields path,startline,data`; on 15.11 the docs gate this behind Premium + Elasticsearch — if it 400s/403s or returns nothing on a term you know exists, fall back to tree (`recursive:=true`) + raw files + local grep, and remember the outcome for the rest of the turn.
8. **Archive** — `…/repository/archive.zip sha=main path=docs --out /tmp/docs.zip` (5/min limit).

- [ ] **Step 4: Write references/merge-requests.md**

Sections (source: Appendix A.3 and the critique):

1. **Find MRs** — `gl api GET /projects/:project/merge_requests state=opened order_by=updated_at --fields iid,title,author.username,source_branch,draft,detailed_merge_status,web_url`; filters `source_branch=`, `target_branch=`, `author_username=`, `reviewer_username=`, `search=`, `wip=yes|no` (there is no `draft=` filter on 15.11); "the MR for my branch": `source_branch=$(git branch --show-current) state=opened`.
2. **Read one** — `…/merge_requests/<iid> --fields iid,title,description,state,draft,detailed_merge_status,has_conflicts,source_branch,target_branch,head_pipeline.id,head_pipeline.status,head_pipeline.web_url,diff_refs,changes_count,reviewers,labels,web_url`; use `detailed_merge_status` not `merge_status`, `head_pipeline` not `pipeline`; `changes_count` is a string capped at `"1000+"`; `diff_refs` is empty for a few seconds after creation.
3. **Diff** — `gl diff <iid> --files`, `gl diff <iid> --file path`; the `[diff omitted by server …]` marker; `…/merge_requests/<iid>/commits --fields short_id,title`.
4. **Discussions vs notes** — read `…/merge_requests/<iid>/discussions --all --fields id,individual_note,notes.0.author.username,notes.0.body,notes.0.resolvable,notes.0.resolved,notes.0.position.new_path,notes.0.position.new_line` (and `--fields id,notes` for the full threads); unresolved threads = `notes.0.resolvable && !notes.0.resolved`. Write (confirm first): new top-level comment `POST …/<iid>/notes body='…'`; new thread on a diff line `POST …/<iid>/discussions body='…' position:='{"position_type":"text","base_sha":…,"start_sha":…,"head_sha":…,"old_path":"p","new_path":"p","new_line":42}'` with SHAs from `diff_refs`; reply `POST …/discussions/<40-hex id>/notes body='…'`; resolve `PUT …/discussions/<id> resolved:=true`. The Notes API can't reply to threads.
5. **Create** — the checklist to gather first (branch pushed? target branch? title? description? draft? labels? reviewers?), then (confirm first) `POST /projects/:project/merge_requests source_branch=feature/x target_branch=main title='Draft: Add X' description='…' remove_source_branch:=true squash:=true labels=backend,needs-review reviewer_ids:=[12]`. Draft is the `Draft:` title prefix (no boolean; `WIP:` is gone). Reviewer ids: `gl api GET /projects/:project/members/all query=jane --fields id,username,name,access_level` (only assignable people; `/users` shows everyone). Labels: validate with `…/labels search=… --fields name`. 409 = an MR for that branch already exists. Default to one reviewer unless verified on the instance (multiple assignees is a Premium feature; behaviour for multiple reviewer ids on an unlicensed EE is unverified).
6. **Update** — (confirm first) `PUT …/merge_requests/<iid> title='…'`, `description='…'`, `add_labels=x`, `remove_labels=y` (never `labels=` — it replaces the whole set), `reviewer_ids:=[12]` (an empty list **unsets all**; omit to keep), `state_event=close|reopen`, `target_branch=`. Mark ready = remove the `Draft:` prefix from the title.
7. **Pipelines for an MR** — prefer `head_pipeline` from the MR; `…/merge_requests/<iid>/pipelines` is minimal (`id, sha, ref, status`).

- [ ] **Step 5: Write references/pipelines.md**

Sections (source: Appendix A.4, critique):

1. **Find the pipeline** — for a branch `gl api GET /projects/:project/pipelines ref=main order_by=updated_at sort=desc per_page=5 --fields id,status,source,sha,created_at,web_url` (or `…/pipelines/latest ref=main`); for an MR: `head_pipeline` from the MR; a single `…/pipelines/<id> --fields id,status,ref,sha,duration,web_url,user.username`. Child pipelines are **not** in the list on 15.11 — see bridges.
2. **Jobs and stages** — `…/pipelines/<id>/jobs --all --fields id,name,stage,status,allow_failure,duration,web_url`; `scope[]=failed` to filter; `include_retried:=true` to see superseded attempts; there is no stages endpoint — group by `stage`. Bridges: `…/pipelines/<id>/bridges --fields id,name,status,downstream_pipeline.id,downstream_pipeline.status` and recurse with the child id (job/pipeline endpoints accept child ids).
3. **Logs** — `gl log <job_id> --sections` → `--section step_script --tail 80` → `--grep 'error|failed|exception|traceback' -C 3`; `--refresh` for a running job; sizes: runner truncates at 4 MB by default, instance at 100 MB.
4. **Retry / cancel / play** (go and report) — `POST …/jobs/<id>/retry` (new job id; the old one becomes `retried`; bridge jobs can't be retried on 15.11 — retry the pipeline), `POST …/jobs/<id>/cancel`, `POST …/jobs/<id>/play job_variables_attributes:='[{"key":"DEPLOY_ENV","value":"staging"}]'` (manual jobs only), `POST …/pipelines/<id>/retry` (reruns failed/canceled jobs, same id), `POST …/pipelines/<id>/cancel` (may return 200 with nothing cancelled — re-fetch). 403 = role below Developer or a protected branch.
5. **Trigger** (go and report; echo ref + variables) — `POST /projects/:project/pipeline ref=main variables:='[{"key":"RUN_E2E","value":"1"}]'` — the path is **singular**; the response's `source` is `api`.
6. **Test reports** — `…/pipelines/<id>/test_report_summary --fields total` then `…/test_report --out /tmp/report.json` (the full report is large — `--out` and `--fields` don't combine; grep the file for `"status": "failed"`).
7. **Artifacts** — `gl artifacts <job_id> --list`, `--file reports/junit.xml`, `--extract DIR`; by ref `gl artifacts --ref main --job build --list` (latest *successful* pipeline only; SHAs rejected); `POST …/jobs/<id>/artifacts/keep`.
8. **Status vocabulary** — jobs: created/pending/running/failed/success/canceled/skipped/waiting_for_resource/manual; pipelines add preparing/scheduled. Don't filter on newer values.

- [ ] **Step 6: Write references/safety-perimeter.md**

Sections:

1. **The allow-list** — reproduce the table from spec §4.3 verbatim (the same paths as `WRITE_ALLOW` in `http.py`), state that it is enforced in code (exit 3), that `DELETE`/`PATCH` don't exist in `gl` at all, and that a `sudo` parameter (query, params, or `--json` body) is refused in code on every verb.
2. **Why each refused class is refused** — merge/approve (authority), rebase (rewrites history), delete/erase (irreversible), repository writes (git does it, with a local diff to review), settings/members/variables/tokens (blast radius), `sudo` (impersonation).
3. **Confirmation format** — the block Claude shows before a confirm-class write:
   ```
   About to POST a comment on platform/api!142 (thread 6a1f…):
   > Looks good — the retry handles the timeout case now.
   Command: gl api POST /projects/:project/merge_requests/142/discussions/6a1f…/notes body='…'
   Send it?
   ```
4. **Error semantics** — 404 is ambiguous (missing vs not visible; wrong path; a proxy that decoded `%2F`); 403 `insufficient_scope` = token scope, other 403 = role/protected branch; 401 = token rejected/expired; 409 = duplicate MR; 429 = rate limit (already retried once); plain-text bodies on 429.
5. **What never enters the transcript** — the token (`gl` runs `token_cmd` itself), the settings files (don't `cat` them), unbounded job logs.

- [ ] **Step 7: Write references/v15-compat.md**

Two lists:

1. **Don't use on 15.11** (each with the version it arrived and the 15.11 alternative): `unidiff` on diffs (18.4 → `gl diff` renders unified text itself); `collapsed`/`too_large` flags (18.4 → empty `diff` string); `inputs` on `POST /pipeline` (17.10 → `variables`); `job_inputs` on retry/play (18.10 → `job_variables_attributes` on play only); `/pipelines/:id/trigger_jobs` (19.2 → `/bridges`); `/jobs/:id/artifacts/tree` (18.8 → download + `gl artifacts --list`); `search_recent_successful_pipelines` (18.7 → job-id routes); `draft=` MR filter (19.0 → `wip=yes|no`); pipeline `name` filter (feature-flagged); `source=parent_pipeline` (17.0 → `/bridges`); `HEAD` as a ref shortcut on files (pass the branch).
2. **Deprecated but present — read the new field**: `merge_status` → `detailed_merge_status`; `merged_by` → `merge_user`; `work_in_progress` → `draft`; `reference` → `references`; `/changes` → `/diffs` (use `/changes?access_raw_diffs=true` only to detect `overflow`); MR `pipeline` → `head_pipeline` (the full object, with `status` and `web_url`); `tag_list` → `topics`; `confidential` note param → `internal` (renamed in 16.0).

- [ ] **Step 8: Validate and commit**

Run: `claude plugin validate plugins/gitlab-client`
Expected: success, skill discovered.

Sanity-check every `gl` invocation in the six files by running it with `--help`-level parsing where possible, e.g. `plugins/gitlab-client/scripts/gl api --help`, `gl log --help` — flags named in the docs must exist.

```bash
git add plugins/gitlab-client/skills
git commit -m "gitlab-client — skill: SKILL.md + six 15.11 reference docs"
```

---

### Task 11: Subagent `pipeline-debugger`

**Files:**
- Create: `plugins/gitlab-client/agents/pipeline-debugger.md`

**Interfaces:**
- Consumes: `gl` (path passed in by the caller), the pipelines reference.
- Produces: the agent file; `SKILL.md` already links to it.

- [ ] **Step 1: Write the agent file**

`agents/pipeline-debugger.md`:

````markdown
---
name: pipeline-debugger
description: Read-only GitLab CI triage driven by the bundled `gl` CLI. Use when a pipeline or job has failed and the question is "why" — it walks the pipeline (including child pipelines), reads the failing jobs' logs section by section, correlates with the MR diff when there is one, and returns a compact report with the decisive log excerpt, the probable cause, and a suggested next step. Do NOT use for a single job log you already know the id of (run `gl log` inline), for anything that needs a write (it refuses — retry/cancel/play are recommendations only), or for non-GitLab CI.
tools: Bash, Read, Grep, Glob
model: sonnet
---

# Pipeline Debugger

Read-only triage of a failed GitLab pipeline through `gl`. Answers one question with the fewest calls that settle it, then returns a report — not the logs.

## Inputs the caller must provide

1. **The `gl` path** — an absolute path to the plugin's `scripts/gl`. Use it verbatim; don't search for it.
2. **The project** — a `group/name` path (pass as `--project group/name` on every call) or "use the repo's origin".
3. **A starting point** — one of: pipeline id · job id · branch name · MR iid.
4. **The question** — e.g. "why is the test stage red", "is this failure caused by MR 142's changes".

Missing any of 1–3 → stop and ask. Never read `.claude/gitlab-client.local.md` or `~/.claude/gitlab-client.local.md`; never run the user's `token_cmd`; `gl` handles auth.

## Perimeter (non-negotiable)

- **Read-only.** You may run `gl version`, `gl project`, `gl api GET …`, `gl log`, `gl diff`, `gl artifacts`. You never run `gl api POST/PUT`. If the fix is "retry job 123" or "cancel pipeline 99", write that as the recommendation.
- **Bounded output.** Never `gl log … --tail 0`. Never paste more than 20 log lines per job into the report.
- Refuse anything else the caller's wording might seem to authorise; say what you refused and why.

## Method

1. **Resolve the pipeline.**
   - pipeline id → `gl api GET /projects/:project/pipelines/<id> --fields id,status,ref,sha,source,web_url`
   - MR iid → `gl api GET /projects/:project/merge_requests/<iid> --fields iid,title,source_branch,head_pipeline.id,head_pipeline.status,head_pipeline.web_url`
   - branch → `gl api GET /projects/:project/pipelines ref=<branch> order_by=updated_at sort=desc per_page=1 --fields id,status,sha,web_url`
   - job id → `gl api GET /projects/:project/jobs/<id> --fields id,name,stage,status,pipeline.id`
2. **List jobs.** `gl api GET /projects/:project/pipelines/<id>/jobs --all --fields id,name,stage,status,allow_failure,duration,web_url`. Then `…/pipelines/<id>/bridges --fields name,status,downstream_pipeline.id,downstream_pipeline.status` and repeat this step for every failed downstream pipeline (15.11 doesn't list child pipelines otherwise).
3. **Read each failed job's log, narrowest view first.**
   - `gl log <job> --sections` — find the failing section (usually `step_script`; look at durations and line counts).
   - `gl log <job> --section step_script --grep 'error|failed|exception|traceback|fatal|exit code' -C 3`
   - `gl log <job> --section step_script --tail 60` if the grep is inconclusive.
   - Runner/infrastructure failures live in `prepare_executor` / `get_sources`; `allow_failure: true` jobs don't fail the pipeline — say so and move on.
4. **Correlate with the change when there is an MR.** `gl diff <iid> --files`, then `--file PATH` for files named in the log. Distinguish "the change broke it" from "flaky / infra / unrelated".
5. **Stop when you can name the cause.** Don't read logs of passing jobs. Don't fetch artifacts unless the log points at a report file (then `gl artifacts <job> --file <path>`).

## Report format (return exactly this shape)

```
Pipeline <id> on <ref> — <status> · <web_url>
Failed jobs: <n> (<names>)   Child pipelines checked: <k>

## <job name> (stage <stage>, job <id>) · <web_url>
Decisive excerpt (<section>, lines <a>–<b>):
    <≤ 20 verbatim log lines>
Probable cause: <one or two sentences>
Related change: <file:line from the MR diff, or "none / not an MR pipeline">
Next step: <one concrete action — e.g. "fix X then push", or "flaky: gl api POST /projects/:project/jobs/<id>/retry" (for the caller to run)>

(repeat per failed job, most likely root cause first)

Confidence: high | medium | low — <why>
```

Keep the whole report under ~60 lines. If you could not resolve a pipeline or a log 404s, say exactly which call failed and with what message.
````

- [ ] **Step 2: Validate and commit**

Run: `claude plugin validate plugins/gitlab-client`
Expected: success, agent discovered.

```bash
git add plugins/gitlab-client/agents
git commit -m "gitlab-client — pipeline-debugger subagent (read-only CI triage)"
```

---

### Task 12: `setup-checklist.md`, `/gitlab-client:setup`, `/gitlab-client:doctor`

**Files:**
- Create: `plugins/gitlab-client/setup-checklist.md`
- Create: `plugins/gitlab-client/commands/setup.md`
- Create: `plugins/gitlab-client/commands/doctor.md`

**Interfaces:**
- Consumes: `gl version`, `gl api GET /personal_access_tokens/self`, `gl project`, exit codes from Task 6.
- Produces: the 8-check checklist both commands read via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`.

- [ ] **Step 1: Write setup-checklist.md**

````markdown
# Setup checklist (shared reference)

Read by `/gitlab-client:setup` and `/gitlab-client:doctor` via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`. Single source of truth for the 8 checks and their remediation text — `doctor` reports all of them, `setup` runs the static ones it owns (1, 2, 3, 8) and writes the settings files. Lives at the plugin root (not under `commands/`) so it isn't itself a slash command.

Checks 1–3 and 8 are static and read-only. Checks 4–7 are **live**: they run the user's `token_cmd` (inside `gl`) and call the GitLab API. `gl` is `${CLAUDE_PLUGIN_ROOT}/scripts/gl`; run every check from the project root. None needs `sudo`. Statuses: `PASS`, `WARN` (works, but say why), `FAIL`, `SKIP` (a prerequisite failed), `n/a` (doesn't apply).

## Checks

### 1. `python3` ≥ 3.10 on PATH

- Run: `python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'`
- PASS if exit 0.
- FAIL → "gitlab-client needs `python3` 3.10 or newer on PATH (it runs the bundled client with the system interpreter — no venv). Debian/Ubuntu: `sudo apt install python3`; macOS: `brew install python`."

### 2. User settings file present

- Run: `test -f ~/.claude/gitlab-client.local.md`
- PASS if it exists.
- **Env-only configuration:** if both `GITLAB_CLIENT_URL` and `GITLAB_CLIENT_TOKEN` are exported (`test -n "$GITLAB_CLIENT_URL" && test -n "$GITLAB_CLIENT_TOKEN"`), report checks 2 **and 3** as `n/a — configured via environment` and still run checks 4–7 (they only need `gl`, which reads the env).
- FAIL → "No `~/.claude/gitlab-client.local.md`. Run `/gitlab-client:setup` — it records the GitLab URL and the command that prints your access token. (Or export `GITLAB_CLIENT_URL` and `GITLAB_CLIENT_TOKEN`.)"

### 3. Settings valid (only if 2 passed; `n/a` when env-only)

- **Never `Read` or `cat` the settings files** — a hand-written `token_cmd` may contain the token itself, and `Read` output lands in the transcript. Use silent greps:
  - `grep -qE '^url: *https?://' ~/.claude/gitlab-client.local.md .claude/gitlab-client.local.md 2>/dev/null` → url present in either file
  - `grep -qE '^token_cmd: *[^ ]' ~/.claude/gitlab-client.local.md .claude/gitlab-client.local.md 2>/dev/null` → token_cmd present in either file
  - `grep -ohE '^[A-Za-z_]+:' ~/.claude/gitlab-client.local.md .claude/gitlab-client.local.md 2>/dev/null | sort -u` → the key names in use (prints only names, never values)
- PASS if the first two greps exit 0 and the key names are a subset of `url:`, `token_cmd:`, `project:`.
- FAIL → "`~/.claude/gitlab-client.local.md` needs `url: https://…` and `token_cmd: <command printing the token>` in its frontmatter. Re-run `/gitlab-client:setup`, or see `skills/gitlab-client/references/local-settings.md`." (For an unknown key: "unknown key `<k>` in a gitlab-client settings file — only `url`, `token_cmd`, `project` are read.")

### 4. `token_cmd` resolves (live; only if 3 passed or is `n/a`)

- Run: `"${CLAUDE_PLUGIN_ROOT}/scripts/gl" version; echo "exit=$?"`
- PASS if the exit code is **not 2** (an exit of 0 or 1 means the token was obtained — 1 is a network/HTTP problem for check 5). Keep the stdout/stderr for check 5.
- **Never print the token.** `gl` never prints it either; its error text shows only `token_cmd`'s stderr and exit code.
- FAIL (exit 2) → show `gl`'s stderr line, then: "`token_cmd` did not produce a token. Common causes: the secret-store CLI isn't logged in, the secret path is wrong, or the command prints more than one line. Fix `token_cmd` in `~/.claude/gitlab-client.local.md` or re-run `/gitlab-client:setup`."

### 5. Instance reachable, version (live; only if 4 passed)

- Uses the same `gl version` run. PASS if exit 0; note `version` and `enterprise` for the summary.
- WARN (still PASS for the summary count) if `gl` printed `references are written for GitLab 15.11; this instance is …` → repeat that line.
- FAIL (exit 1) → show `gl`'s stderr, then: "Could not reach `<url>` as an authenticated user. 401 means the token was rejected (expired, revoked, or the wrong secret); a connection error means the host isn't reachable from here (VPN? DNS? proxy?); 404 on `/api/v4/metadata` usually means `url` points at the wrong place (relative-URL installs need the `/gitlab` prefix)."

### 6. Token scopes and expiry (live; only if 5 passed)

- Run: `"${CLAUDE_PLUGIN_ROOT}/scripts/gl" api GET /personal_access_tokens/self --fields name,scopes,expires_at,active,revoked`
- PASS if `active` is true, `revoked` is false, and `scopes` contains `api` or `read_api`.
- WARN if `scopes` has `read_api` but not `api` → "Token is read-only (`read_api`): browsing, logs, and diffs work; creating MRs, commenting, retrying jobs and triggering pipelines will fail with 403. Create a token with the `api` scope if you want writes."
- WARN if `expires_at` is within 14 days → "Token expires on `<date>` — rotate it soon and update `token_cmd`'s secret."
- WARN if the call itself 403s/404s (check 5 already proved the token works) → "Could not introspect the token (`/personal_access_tokens/self` needs GitLab ≥ 15.5 and may need `read_api`). Check the token's scopes in GitLab → User settings → Access tokens: `read_api` for reads, `api` for writes."
- FAIL if `active` is false or `revoked` is true → "The token is revoked or inactive. Create a new one and update the secret behind `token_cmd`."

### 7. Project resolves (live; only if 5 passed)

- Run: `"${CLAUDE_PLUGIN_ROOT}/scripts/gl" project; echo "exit=$?"`
- PASS if exit 0; note `path_with_namespace` and `resolved_from`.
- Exit 2 — map `gl`'s message:
  - "…`git remote get-url origin` gave nothing" → `n/a` — "not in a GitLab clone; pass `--project group/name` when calling `gl`, or set `project:` in `.claude/gitlab-client.local.md`".
  - "…doesn't look like a GitLab project path" → FAIL → "The `origin` remote isn't a GitLab project URL. Set `project: group/name` in `.claude/gitlab-client.local.md` (`/gitlab-client:setup` can write it) or pass `--project`."
  - "…not found or not visible" → FAIL → "The remote's path doesn't match a project this token can see. Either the project path differs from the git remote (set `project: group/name` in `.claude/gitlab-client.local.md` — `/gitlab-client:setup` can write it) or the token's user isn't a member."
- Exit 1 → FAIL with `gl`'s stderr (an API error other than 404; see check 5's causes).

### 8. `.gitignore` covers the project settings file (only if `.claude/gitlab-client.local.md` exists)

- Check the project's `.gitignore` for `.claude/*.local.md` (or an exact `.claude/gitlab-client.local.md` entry).
- PASS if covered; `n/a` if there is no project-level file.
- FAIL → "Add `.claude/*.local.md` to `.gitignore` — the settings file can name secret-store paths and shouldn't be committed."
````

- [ ] **Step 2: Write commands/setup.md**

````markdown
---
description: Configure gitlab-client — record the GitLab URL and the command that prints your access token in ~/.claude/gitlab-client.local.md, optionally pin a project path for this repo, and add the project file to .gitignore. Never runs your token command and never contacts GitLab.
allowed-tools: Bash(python3 -c *), Bash(test -f *), Bash(grep *), Bash(git remote get-url *), Read, Glob, Write, Edit, AskUserQuestion
---

You are running `/gitlab-client:setup`: an interactive configurator. You will write **only** `~/.claude/gitlab-client.local.md`, optionally `.claude/gitlab-client.local.md` in the project root, and (if needed) a line in the project's `.gitignore`. You will **not** run `token_cmd`, **not** call `gl`, and **not** contact GitLab — `/gitlab-client:doctor` does the live checks afterwards.

First read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` — it defines checks 1–8 and the remediation text. Use that text verbatim on failures.

## Flow

### 1. Prerequisite
Run check 1 (`python3` ≥ 3.10). If it fails, print its remediation text and **stop**.

### 2. Existing user settings?
Run check 2. If `~/.claude/gitlab-client.local.md` exists, run `grep -E '^url:' ~/.claude/gitlab-client.local.md` and `grep -qE '^token_cmd: *[^ ]' ~/.claude/gitlab-client.local.md; echo "token_cmd set: $?"` (0 = set). Show the URL and whether `token_cmd` is set — **don't `Read` or print the file**; a hand-written `token_cmd` could hold the token itself. Ask via **AskUserQuestion**: *Keep as-is* / *Reconfigure (overwrite)* / *Abort*. On Keep, jump to step 5; on Abort, stop.

### 3. GitLab URL
Ask for the instance URL (e.g. `https://gitlab.example.com`; relative-URL installs include the prefix, e.g. `https://intranet.example.com/gitlab`). Strip a trailing slash. It must start with `http://` or `https://`.

### 4. Where does the token come from?
Ask via **AskUserQuestion** — "Where does your GitLab personal access token come from?":

- **Keeper (ksm)** — "`ksm secret notation keeper://<record-uid>/field/password`"
- **pass / 1Password / other CLI** — "`pass show <path>`, `op read \"op://<vault>/<item>/credential\"`, or any command whose stdout is the token."
- **Environment variable** — "The token is already exported, e.g. `$GITLAB_TOKEN` → `printf '%s' \"$GITLAB_TOKEN\"`."

If the user offers to paste the token itself, decline: the file would then *be* the secret and later `/setup` / `/doctor` runs could echo it. Point them at the environment-variable form (export it from their shell profile or a secret-store hook).

Open `${CLAUDE_PLUGIN_ROOT}/skills/gitlab-client/references/local-settings.md` and show the matching example. Ask for the specifics and assemble the final `token_cmd`. Restate it and confirm before writing.

Enforce while assembling:

- The command's **stdout must be exactly one line: the token**. No banners, no log lines (`--quiet` flags where the tool has them).
- Record the command **verbatim** — exactly as the user runs it, concrete values and all. Don't invent env-var placeholders.
- Tell the user which scope the token needs: `read_api` for reading, `api` for creating MRs / commenting / retrying jobs / triggering pipelines.

Write `~/.claude/gitlab-client.local.md` (create `~/.claude/` if needed):

```markdown
---
# Generated by /gitlab-client:setup. Edits take effect on the next gl call — no restart.
# token_cmd stdout must be exactly one line: the personal access token.
url: <url>
token_cmd: <value>
---

# GitLab notes (for humans — not read by the plugin)

Which instance, which token, who to ping when it breaks.
```

Multi-line `token_cmd` → YAML block scalar (`token_cmd: |` + indented lines).

### 5. Project-level file (optional)
Run `git remote get-url origin` in the project root. If it succeeds and looks like `<host>:group/name.git` or `https://<host>/group/name.git`, tell the user the project will be derived from it and ask via **AskUserQuestion** whether to pin it anyway: *No — derive from the remote* / *Yes — pin `project:`* / *Yes — and override url/token_cmd for this repo*. If the remote is missing or doesn't look like a GitLab path, recommend pinning and ask for the `group/name` path.

If writing, create `.claude/gitlab-client.local.md`:

```markdown
---
# Generated by /gitlab-client:setup. Only url, token_cmd, project are read.
project: <group/name>
---
```

Then run check 8; if `.gitignore` lacks `.claude/*.local.md`, append it (create `.gitignore` if absent) and say so.

### 6. Finish
Print what was written (paths only — never the token), then: "Run `/gitlab-client:doctor` to verify the token, the connection, and the project."
````

- [ ] **Step 3: Write commands/doctor.md**

````markdown
---
description: Diagnose the gitlab-client setup — python3, settings files, token_cmd resolves, instance reachable and its version, token scopes and expiry, project resolves from the git remote, .gitignore — and report what is missing. Runs your token_cmd (inside gl) and makes three read-only API calls.
allowed-tools: Bash(python3 -c *), Bash(test -f *), Bash(test -n *), Bash(grep *), Read, Glob
---

You are running `/gitlab-client:doctor`: a health check. Write no files, dispatch no agent, make no write calls. Checks 1–3 and 8 are static. Checks 4–7 are **live** — they run `${CLAUDE_PLUGIN_ROOT}/scripts/gl`, which runs the user's `token_cmd` internally and calls `GET /metadata`, `GET /personal_access_tokens/self`, and `GET /projects/:path`. The three `gl` calls aren't pre-approved, so each prompts for permission once — that is expected. `Read` is for `.gitignore` only; the settings files are inspected with silent `grep`s (see the checklist) so a `token_cmd` never enters the transcript.

## Steps

1. Read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` — it defines checks 1–8 with exact commands and remediation text.

2. Run checks **1–8 in order** from the project root:
   - 1 `python3` ≥ 3.10
   - 2 user settings file present (`n/a` when `GITLAB_CLIENT_URL` + `GITLAB_CLIENT_TOKEN` are both exported)
   - 3 settings valid (SKIP if 2 failed; `n/a` when env-only — then 4–7 still run)
   - 4 `token_cmd` resolves — live (SKIP if 3 failed)
   - 5 instance reachable + version — live (SKIP if 4 failed; WARN on a non-15.x version)
   - 6 token scopes and expiry — live (SKIP if 5 failed; WARN on read-only or expiring tokens)
   - 7 project resolves — live (SKIP if 5 failed; `n/a` outside a GitLab clone)
   - 8 `.gitignore` covers the project settings file (`n/a` if there is no project-level file)

   **Never print a token.** `gl` doesn't either; if you ever see one in output, stop and tell the user their `token_cmd` is misconfigured (it should print only to stdout, and `gl` swallows stdout).

3. Print one line per check:
   ```
   PASS  1. python3 3.12.3
   PASS  2. user settings file present
   PASS  3. settings valid
   PASS  4. token_cmd resolves
   PASS  5. instance reachable — GitLab 15.11.13-ee (EE)
   WARN  6. token scopes — read_api only (writes will 403); expires 2026-09-03
         → <remediation text from the checklist, verbatim>
   PASS  7. project resolves — platform/api (from remote:origin)
   n/a   8. .gitignore — no project-level settings file
   ```

4. End with a one-line summary:
   - All PASS/WARN/n-a → `OK — gitlab-client ready · <host> · GitLab <version> · scopes <list> · project <path or "pass --project">` (append ` · <n> warning(s) above` when any WARN).
   - Settings absent → `Not configured — run /gitlab-client:setup`.
   - Otherwise → `<n> issue(s) — see the FAIL lines above`.

Keep it terse. Don't fix anything here — point at `/gitlab-client:setup` only where the checklist's remediation text already does.
````

- [ ] **Step 4: Validate and commit**

Run: `claude plugin validate plugins/gitlab-client`
Expected: success; two commands discovered; `setup-checklist.md` is **not** listed as a command.

Dry-run the checklist's live commands by hand against the stub-free CLI to be sure they parse: `plugins/gitlab-client/scripts/gl api GET /personal_access_tokens/self --fields name,scopes,expires_at,active,revoked; echo $?` → exit 2 (no settings) is the expected result here, not a usage error.

```bash
git add plugins/gitlab-client/setup-checklist.md plugins/gitlab-client/commands
git commit -m "gitlab-client — /setup + /doctor over a shared 8-check checklist"
```

---

### Task 13: README, plugin CLAUDE.md, uv.lock, all gates

**Files:**
- Create: `plugins/gitlab-client/README.md`
- Create: `plugins/gitlab-client/CLAUDE.md`

- [ ] **Step 1: Write README.md**

Sections and required content (user-facing; mirror `mysql-client/README.md`'s tone):

1. **Title + one paragraph** — what it is (on-prem GitLab 15.x, repos / MRs / CI through a bundled REST client), what it deliberately won't do (merge, approve, delete, settings).
2. **Quick start** — the three commands:
   ```bash
   /plugin marketplace add aeresov/claude-plugins
   /plugin install gitlab-client@aeresov-claude-plugins
   /gitlab-client:setup      # URL + token command → ~/.claude/gitlab-client.local.md
   /gitlab-client:doctor     # verify token, connection, project
   ```
   then "ask Claude about an MR, a pipeline, or a file in a GitLab project".
3. **Prerequisites** — `python3` ≥ 3.10; a personal access token: `read_api` (reads) or `api` (writes); no other installs.
4. **Settings** — the two files, the three keys, the minimum user file, `token_cmd` rules and examples (Keeper, pass, op, env var), env overrides, `.gitignore` for the project file.
5. **What Claude will and won't do** — the three write classes as a table (refused / confirmed / go-and-report).
6. **The `gl` CLI** — the cheat-sheet from SKILL.md, exit codes, `--fields`, `:project`.
7. **Pipeline triage** — one paragraph on `pipeline-debugger`.
8. **Troubleshooting** — 404 is ambiguous; 403 role vs scope; huge job logs (`--sections`); artifact downloads 302 to object storage (token is stripped); "references are written for 15.11" warning; cache location `~/.cache/gitlab-client/<host>/` and that it's safe to delete.
9. **License** — AGPL-3.0-only, link to repo-root `LICENSE`.

- [ ] **Step 2: Write CLAUDE.md**

Developer notes; same headings as `plugins/mysql-client/CLAUDE.md`:

1. **What this plugin is** — one paragraph; "read `README.md` for user docs, repo-root `CLAUDE.md` for marketplace conventions".
2. **Layout** — one bullet per path from the plan's file-structure section, each with its responsibility (copy the responsibility phrases from the "File structure" block above).
3. **Commands**:
   ```bash
   claude plugin validate .                              # marketplace
   claude plugin validate plugins/gitlab-client          # this plugin
   cd scripts/gitlab-client && uv sync --group dev && uv run pytest -q   # unit tests (stub transport, no GitLab)
   GITLAB_CLIENT_LIVE=1 GITLAB_CLIENT_URL=… GITLAB_CLIENT_TOKEN=… uv run pytest -q tests/test_live.py   # optional live smoke (added in Task 14)
   ./scripts/gl version                                  # run the CLI without a venv
   ```
4. **Gotchas** — each as a bold lead-in + explanation:
   - **The token never touches a command line.** `settings.resolve_token` runs `token_cmd` in-process; error paths show stderr only. Don't add a `--token` flag.
   - **The write allow-list lives in two places.** `http.WRITE_ALLOW` (enforced) and `references/safety-perimeter.md` (documented). Change both; `tests/test_http.py::ALLOWED/REFUSED` pins the code side.
   - **`/repository/tree` is keyset-only** since 15.0 — `Client.paginate` switches automatically on that path; don't add `page=` there.
   - **Cross-host redirects drop auth.** `AuthStrippingRedirectHandler` — artifact/trace downloads 302 to object storage. Keep `stream_to` downloads going through `Client.request`.
   - **References target 15.11.** `v15-compat.md` is the list of things not to add; when the instance is upgraded, re-verify Appendix A of the spec before editing the references.
   - **Subagent rules are duplicated, not linked.** `agents/pipeline-debugger.md` repeats the perimeter because subagents don't inherit skill context.
   - **Version bumps touch four files:** `.claude-plugin/plugin.json`, the repo-root `marketplace.json` entry, `scripts/gitlab-client/pyproject.toml`, `uv.lock` — plus `src/gitlab_client/__init__.py::__version__` (pinned to pyproject by `tests/test_version.py`).

- [ ] **Step 3: Run every gate**

```bash
cd plugins/gitlab-client/scripts/gitlab-client && uv sync --group dev && uv run pytest -q && cd -
claude plugin validate .
claude plugin validate plugins/gitlab-client
scripts/check-version-sync.sh
cd plugins/gitlab-client/scripts/gitlab-client && uv sync --locked --group dev && uv run pytest -q
```

Expected: `uv.lock` unchanged since Task 1; all tests pass; both validations succeed; version-sync prints nothing and exits 0; `--locked` sync succeeds.

Also confirm the launcher works from any cwd: `cd /tmp && /home/…/plugins/gitlab-client/scripts/gl --version` → `gl 0.1.0`.

- [ ] **Step 4: Commit**

```bash
git add plugins/gitlab-client
git commit -m "gitlab-client 0.1.0 — README, developer notes, uv.lock"
```

---

### Task 14: Live verification against the real 15.11 instance (spec Appendix B)

**Files:**
- Create: `plugins/gitlab-client/scripts/gitlab-client/tests/test_live.py`
- Modify: `plugins/gitlab-client/CLAUDE.md` (add a "Verified on the instance" section)
- Possibly modify: `skills/gitlab-client/references/*.md` (if a probe contradicts the docs)

**Prerequisite:** the user has run `/gitlab-client:setup` (or exported `GITLAB_CLIENT_URL`/`GITLAB_CLIENT_TOKEN`) and `/gitlab-client:doctor` is green. This task needs their instance; if it isn't reachable, do steps 1–2, leave step 3 as a documented TODO in `CLAUDE.md` with the exact commands, and say so in the final report.

- [ ] **Step 1: Write the opt-in live smoke test**

`tests/test_live.py`:

```python
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
```

Run: `uv run pytest -q tests/test_live.py` → `1 skipped` (no env). With the env set: `1 passed`.

- [ ] **Step 2: Run the Appendix B probes with `gl` and record the answers**

Run each from a clone of a real project (use `--project group/name` otherwise). Record the result of every probe under a new `## Verified on the instance (<date>, GitLab <version>)` section in `plugins/gitlab-client/CLAUDE.md`, one bullet each:

1. Scope of `/metadata` and `/personal_access_tokens/self` with a `read_api`-only token:
   `gl version` and `gl api GET /personal_access_tokens/self --fields scopes` — note whether either 401/403s. If `/self` fails for `read_api`, change check 6 in `setup-checklist.md` to WARN with "cannot introspect" wording (already provided there).
2. Range support on traces: `gl api GET /projects/:project/jobs/<id>/trace --out /tmp/t.log` then, with curl by hand (the client doesn't send Range): `curl -sS -H "PRIVATE-TOKEN: $(…)" -H "Range: bytes=-1024" -o /dev/null -w '%{http_code}\n' <url>/api/v4/projects/<id>/jobs/<jid>/trace` — record 206 vs 200. (Don't paste the token into the transcript; run it with `! curl …` yourself.)
3. Code search: `gl api GET /projects/:project/search scope=blobs search=<a word you know exists> --fields path,startline` — record the status/body; adjust `repo-browsing.md` §7 if the instance returns results without Elasticsearch.
4. Non-retryable retry / non-manual play: `gl api POST /projects/:project/jobs/<successful job id>/retry` and `gl api POST /projects/:project/jobs/<non-manual job id>/play` — record the status and message; add them to `pipelines.md` §4.
5. Multiple reviewers on this license: on a throwaway MR, `gl api PUT /projects/:project/merge_requests/<iid> reviewer_ids:=[<id1>,<id2>]` then read back `reviewers` — record accept / truncate / 400; update `merge-requests.md` §5.
6. Unknown label via `add_labels`: `gl api PUT … add_labels=zz-probe-label` then read `labels`; delete the label in the UI afterwards if it was auto-created; record in `merge-requests.md` §6.
7. Object-storage redirect: `gl artifacts <job with artifacts> --list` — confirm it succeeds; if the instance proxies downloads (no 302), note that the redirect handler is untested live.

- [ ] **Step 3: Re-run the gates and commit**

```bash
cd plugins/gitlab-client/scripts/gitlab-client && uv run pytest -q && cd -
claude plugin validate plugins/gitlab-client && scripts/check-version-sync.sh
git add plugins/gitlab-client
git commit -m "gitlab-client — live smoke test; instance probes recorded in CLAUDE.md"
```

---

## Self-review (done while writing; re-check before executing)

- **Verified by execution:** Tasks 1–9 were materialised verbatim from this document into a scratch tree twice; the second run gives 114/114 passing tests, `gl --version` from another cwd, exit 2 without settings, and exit 3 for a refused `:project` write with no network call. Doc tasks 10–14 were reviewed against the spec and the sibling plugins' conventions.

- **Spec coverage:** §2 layout → Tasks 1, 10–13 · §3 settings → Task 2 · §4.1 surface → Task 6 parser · §4.2 `gl api` → Tasks 4, 6 · §4.3 transport/policy → Tasks 3, 4 · §4.4 log → Task 7 · §4.5 diff → Task 8 · §4.6 artifacts → Task 9 · §4.7 project/version → Tasks 5, 6 · §5 skill → Task 10 · §6 agent → Task 11 · §7 commands/checklist → Task 12 · §8 errors → Task 3 (`describe_error`, `_HINTS`) + Task 6 exit codes · §9 testing → every task + Task 14 live · §10 docs → Task 13 · Appendix B → Task 14.
- **Type consistency:** `Client.request(method, path, *, query, json_body, accept_json, timeout, stream_to, absolute_url)` is used identically in Tasks 5–9; `Response.bytes_written` (Task 3) is what `download_archive` and `cmd_api --out` read; `resolve_project(client, settings, *, remote, cwd, run)` matches `Context.project`; `fetch_trace` returns `(job, path, size)` everywhere; `render_files`/`render_diffs` names match between `diff.py` and `cmd_diff`; `PROJECT_JSON` in the CLI-level tests always has `id: 42` so helper paths use `/projects/42/…`.
- **Known deliberate simplifications** (not placeholders): `--raw` keeps ANSI but still collapses `\r`; `grep` line numbers are relative to the section when `--section` is given; a renamed-and-modified file over the diff limit renders as a pure rename; by-ref artifact archives are never cached.
