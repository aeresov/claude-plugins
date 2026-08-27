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
    text = '---\n# comment\nurl: https://gitlab.example.com/\ntoken_cmd: "ksm secret get x"\n---\n\n# notes\nurl: not-parsed\n'
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
    assert "unknown setting 'bogus'" in capsys.readouterr().err  # default warn → stderr

    warnings = []
    load_settings(cwd=cwd, home=home, env={}, warn=warnings.append)
    assert len(warnings) == 1 and "unknown setting 'bogus'" in warnings[0] and capsys.readouterr().err == ""

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
    """Return (runner, calls) — `calls` records every (cmd, kwargs) the runner saw."""
    calls: list[tuple] = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd, 60)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run, calls


def settings(token_cmd="printf %s tok"):
    return Settings(url="https://x", token_cmd=token_cmd, project=None, token=None, sources={})


def test_resolve_token_runs_shell_command():
    run, calls = fake_run(stdout="glpat-abc\n")
    assert resolve_token(settings("ksm secret get x"), run=run) == "glpat-abc"
    cmd, kwargs = calls[0]
    assert cmd == "ksm secret get x"
    assert kwargs["shell"] is True and kwargs["capture_output"] is True and kwargs["timeout"] == 60


def test_resolve_token_failures_never_show_stdout():
    with pytest.raises(ConfigError) as e:
        resolve_token(settings(), run=fake_run(stdout="glpat-leak", stderr="not logged in", returncode=3)[0])
    assert "exited 3" in str(e.value) and "not logged in" in str(e.value) and "glpat-leak" not in str(e.value)

    with pytest.raises(ConfigError, match="exactly one non-empty line \\(got 2\\)"):
        resolve_token(settings(), run=fake_run(stdout="line1\nline2\n")[0])
    with pytest.raises(ConfigError, match="got 0"):
        resolve_token(settings(), run=fake_run(stdout="\n")[0])
    with pytest.raises(ConfigError, match="timed out"):
        resolve_token(settings(), run=fake_run(raise_timeout=True)[0])
