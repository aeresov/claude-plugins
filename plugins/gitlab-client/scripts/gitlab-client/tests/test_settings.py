# SPDX-License-Identifier: AGPL-3.0-only
import fcntl
import os
import shlex
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gitlab_client import settings as settings_module
from gitlab_client.errors import ConfigError
from gitlab_client.settings import (
    ENV_TOKEN,
    ENV_URL,
    TOKEN_CMD_TIMEOUT,
    Settings,
    load_settings,
    parse_frontmatter,
    resolve_token,
    run_token_cmd,
)


def test_parse_plain_and_quoted_values():
    text = '---\n# comment\nurl: https://gitlab.example.com/\ntoken_cmd: "pass show gitlab/pat"\n---\n\n# notes\nurl: not-parsed\n'
    assert parse_frontmatter(text) == {"url": "https://gitlab.example.com/", "token_cmd": "pass show gitlab/pat"}


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


def test_project_file_cannot_set_url_or_token_cmd(tmp_path):
    # A checkout's file must not redirect the user's token to another host, nor run its own command.
    home, cwd = tmp_path / "home", tmp_path / "repo"
    write(home / ".claude/gitlab-client.local.md", "---\nurl: https://user.example\ntoken_cmd: echo u\n---\n")
    write(cwd / ".claude/gitlab-client.local.md", "---\nurl: http://evil.example\ntoken_cmd: touch pwned\nproject: grp/proj\n---\n")
    warnings = []
    s = load_settings(cwd=cwd, home=home, env={}, warn=warnings.append)
    assert (s.url, s.token_cmd, s.project) == ("https://user.example", "echo u", "grp/proj")
    assert s.sources == {"url": "user file", "token_cmd": "user file", "project": "project file"}
    assert len(warnings) == 2 and all("may only set 'project'" in w for w in warnings)
    assert "'url' ignored" in warnings[0] and "'token_cmd' ignored" in warnings[1]

    # With no user file, the project file's url/token_cmd still don't count.
    with pytest.raises(ConfigError, match="no GitLab URL configured"):
        load_settings(cwd=cwd, home=tmp_path / "nohome", env={}, warn=lambda m: None)


def test_precedence_flag_env_project_user(tmp_path, capsys):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    write(home / ".claude/gitlab-client.local.md", "---\nurl: https://user.example\ntoken_cmd: echo u\nproject: user/default\nbogus: 1\n---\n")
    write(cwd / ".claude/gitlab-client.local.md", "---\nproject: grp/proj\n---\n")

    s = load_settings(cwd=cwd, home=home, env={})
    assert (s.url, s.token_cmd, s.project) == ("https://user.example", "echo u", "grp/proj")
    assert s.sources == {"url": "user file", "token_cmd": "user file", "project": "project file"}
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
    assert resolve_token(s, run=lambda *a, **k: pytest.fail("token_cmd must not run")) == "sekrit"  # ty: ignore[invalid-argument-type]  -- ty misreads pytest 8.x's decorated fail()


def test_missing_url_and_token_cmd(tmp_path):
    with pytest.raises(ConfigError, match="no GitLab URL configured.*gitlab-client-setup"):
        load_settings(cwd=tmp_path, home=tmp_path, env={})
    with pytest.raises(ConfigError, match="no token_cmd configured"):
        load_settings(cwd=tmp_path, home=tmp_path, env={ENV_URL: "https://x"})
    with pytest.raises(ConfigError, match="must start with http"):
        load_settings(cwd=tmp_path, home=tmp_path, env={ENV_URL: "gitlab.example.com", ENV_TOKEN: "t"})


def fake_run(stdout="", stderr="", returncode=0, timed_out=False):
    """Return (runner, calls) — `calls` records every (cmd, timeout) the runner saw."""
    calls: list[tuple] = []

    def run(cmd, timeout):
        calls.append((cmd, timeout))
        if timed_out:
            return None
        out = stdout if isinstance(stdout, bytes) else stdout.encode()
        err = stderr if isinstance(stderr, bytes) else stderr.encode()
        return subprocess.CompletedProcess(cmd, returncode, out, err)

    return run, calls


def settings(token_cmd="printf %s tok"):
    return Settings(url="https://x", token_cmd=token_cmd, project=None, token=None, sources={})


def test_resolve_token_runs_token_cmd(tmp_path):
    run, calls = fake_run(stdout="glpat-abc\n")
    assert resolve_token(settings("pass show gitlab/pat"), lock_path=tmp_path / "token.lock", run=run) == "glpat-abc"
    assert calls == [("pass show gitlab/pat", TOKEN_CMD_TIMEOUT)]


def test_resolve_token_failures_never_show_stdout():
    with pytest.raises(ConfigError) as e:
        resolve_token(settings(), run=fake_run(stdout="glpat-leak", stderr="not logged in", returncode=3)[0])
    assert "exited 3" in str(e.value) and "not logged in" in str(e.value) and "glpat-leak" not in str(e.value)

    with pytest.raises(ConfigError, match="exactly one non-empty line \\(got 2\\)"):
        resolve_token(settings(), run=fake_run(stdout="line1\nline2\n")[0])
    with pytest.raises(ConfigError, match="got 0"):
        resolve_token(settings(), run=fake_run(stdout="\n")[0])
    with pytest.raises(ConfigError, match="timed out after 60s") as e:
        resolve_token(settings(), run=fake_run(timed_out=True)[0])
    assert e.value.__context__ is None and e.value.__cause__ is None


def test_resolve_token_refuses_output_that_is_not_utf8():
    # A UnicodeDecodeError would end gl in a traceback, with the token's bytes in its .object.
    with pytest.raises(ConfigError, match="not UTF-8") as e:
        resolve_token(settings(), run=fake_run(stdout=b"glpat-\xff\xfeleak\n")[0])
    assert "leak" not in str(e.value) and e.value.__context__ is None and e.value.__cause__ is None
    with pytest.raises(ConfigError, match="exited 3; stderr: bad \ufffd byte"):
        resolve_token(settings(), run=fake_run(stderr=b"bad \xff byte", returncode=3)[0])


def test_resolve_token_reports_a_shell_that_cannot_start():
    def run(cmd, timeout):
        raise FileNotFoundError(2, "No such file or directory", "/bin/sh")

    with pytest.raises(ConfigError, match="could not start token_cmd: No such file or directory"):
        resolve_token(settings(), run=run)


# --- run_token_cmd: how the command itself is started (real processes) ---

PY = shlex.quote(sys.executable)


def py(code: str) -> str:
    """A token_cmd that runs `code` with this interpreter (-I: nothing from the cwd)."""
    return f"{PY} -I -c {shlex.quote(code)}"


def test_token_cmd_runs_from_the_filesystem_root(tmp_path, monkeypatch):
    # Some secret-store CLIs read the current directory first (Keeper Commander runs a file there named
    # like its command, and takes a config.json there as its config) — gl runs from inside a checkout.
    monkeypatch.chdir(tmp_path)
    proc = run_token_cmd(py("import os; print(os.getcwd())"), 10)
    assert proc is not None and proc.returncode == 0 and proc.stdout.strip() == b"/"


def test_token_cmd_gets_dev_null_on_stdin():
    # Give gl a stdin that isn't /dev/null (the suite's own often is), so inheriting it would show.
    r, w = os.pipe()
    saved = os.dup(0)
    os.dup2(r, 0)
    try:
        proc = run_token_cmd(py("import os; a, b = os.fstat(0), os.stat(os.devnull); print(a.st_dev == b.st_dev and a.st_ino == b.st_ino)"), 10)
    finally:
        os.dup2(saved, 0)
        for fd in (r, w, saved):
            os.close(fd)
    assert proc is not None and proc.stdout.strip() == b"True"


def test_token_cmd_runs_in_a_session_of_its_own():
    # No controlling terminal: a prompt that opens /dev/tty (getpass) fails instead of waiting on a person.
    proc = run_token_cmd(py("import os; print(os.getsid(0))"), 10)
    assert proc is not None and int(proc.stdout) != os.getsid(0)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # reparented to a PID 1 that doesn't reap: a zombie is dead too
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _wait_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_token_cmd_timeout_kills_everything_it_started(tmp_path):
    pidfile = tmp_path / "pid"
    # The shell starts a grandchild; killing only the shell (subprocess.run's way) would orphan it.
    cmd = f"sh -c 'echo $$ > {pidfile}; exec sleep 30' & wait"
    started = time.monotonic()
    assert run_token_cmd(cmd, 0.5) is None
    assert time.monotonic() - started < 10
    assert _wait_dead(int(pidfile.read_text()))


def test_token_cmd_interrupt_kills_everything_it_started(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    real_communicate = subprocess.Popen.communicate

    def interrupted(self, *a, **k):
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_token_cmd(f"sh -c 'echo $$ > {pidfile}; exec sleep 30' & wait", 30)
    monkeypatch.setattr(subprocess.Popen, "communicate", real_communicate)
    assert _wait_dead(int(pidfile.read_text()))


# --- the token lock: one token_cmd at a time across gl processes ---


def hold(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_token_cmd_waits_for_the_lock_and_gives_up_without_running(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "TOKEN_LOCK_WAIT", 0.3)
    lock = tmp_path / "gitlab-client" / "token.lock"
    fd = hold(lock)
    try:
        run, calls = fake_run(stdout="tok\n")
        with pytest.raises(ConfigError, match="waited 0.3s for other gl calls' token_cmd to finish; gave up"):
            resolve_token(settings(), lock_path=lock, run=run)
        assert calls == []
    finally:
        os.close(fd)


def test_token_cmd_runs_once_the_lock_is_free_and_releases_it(tmp_path):
    lock = tmp_path / "gitlab-client" / "token.lock"
    os.close(hold(lock))  # held, then released: the next run must get it
    seen = []

    def run(cmd, timeout):
        probe = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)  # held while token_cmd runs
        finally:
            os.close(probe)
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"tok\n", b"")

    assert resolve_token(settings(), lock_path=lock, run=run) == "tok"
    os.close(hold(lock))  # released afterwards
    assert seen and stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_token_cmd_runs_unlocked_when_the_lock_cannot_be_opened(tmp_path):
    # A sandbox that blocks gl's cache directory must not stop the token from resolving.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    run, calls = fake_run(stdout="tok\n")
    assert resolve_token(settings(), lock_path=blocker / "token.lock", run=run) == "tok"
    assert len(calls) == 1


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_token_cmd_does_not_inherit_the_lock(tmp_path):
    # Else a token_cmd that outlives gl would keep every later gl call waiting.
    lock = tmp_path / "token.lock"
    code = "import os; print(sorted(os.readlink(f'/proc/self/fd/{n}') for n in os.listdir('/proc/self/fd') if os.path.exists(f'/proc/self/fd/{n}')))"
    out = resolve_token(settings(py(code)), lock_path=lock)
    assert str(lock) not in out


def test_token_cmd_lock_owned_by_someone_else_is_not_used(tmp_path, monkeypatch):
    # Anyone who can open a lock file in a shared cache directory could hold it and stall every gl call.
    lock = tmp_path / "token.lock"
    fd = hold(lock)
    try:
        real_uid = os.getuid()
        monkeypatch.setattr(settings_module.os, "getuid", lambda: real_uid + 1)
        run, calls = fake_run(stdout="tok\n")
        assert resolve_token(settings(), lock_path=lock, run=run) == "tok"
        assert len(calls) == 1
    finally:
        os.close(fd)


def test_token_cmd_lock_that_is_a_fifo_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "TOKEN_LOCK_WAIT", 0.3)
    lock = tmp_path / "token.lock"
    os.mkfifo(lock)
    fd = os.open(lock, os.O_RDWR | os.O_NONBLOCK)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # held, as by someone who planted it
    try:
        run, calls = fake_run(stdout="tok\n")
        assert resolve_token(settings(), lock_path=lock, run=run) == "tok"
        assert len(calls) == 1
    finally:
        os.close(fd)


GL_LIKE = """
import sys
from gitlab_client.settings import run_token_cmd
run_token_cmd(sys.argv[1], 30)
"""


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_token_cmd_dies_with_gl_on_a_signal(tmp_path, sig):
    # token_cmd is in a session of its own, so a signal to gl's process group no longer reaches it:
    # gl must kill it on the way out, or it would run on unbounded and outside the lock.
    pidfile = tmp_path / "pid"
    src = Path(settings_module.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src)}
    gl = subprocess.Popen([sys.executable, "-c", GL_LIKE, f"sh -c 'echo $$ > {pidfile}; exec sleep 30' & wait"], env=env, cwd=tmp_path)
    try:
        deadline = time.monotonic() + 10
        while not (pidfile.exists() and pidfile.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.02)
        gl.send_signal(sig)
        assert gl.wait(timeout=10) == 128 + sig
        assert _wait_dead(int(pidfile.read_text()))
    finally:
        if gl.poll() is None:
            gl.kill()


def test_token_cmd_restores_signal_handlers():
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    run_token_cmd("true", 10)
    assert {s: signal.getsignal(s) for s in before} == before
