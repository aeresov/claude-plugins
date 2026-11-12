# SPDX-License-Identifier: AGPL-3.0-only
"""scripts/launch.sh: which venv it builds for the host's system Python, and that it replaces one that doesn't fit.
`uv` and the system interpreter are stubs, so both branches run on any machine; nothing is downloaded or installed."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

LAUNCH = Path(__file__).resolve().parents[1] / "scripts" / "launch.sh"

# What uv records in pyvenv.cfg: the full version for a system interpreter, major.minor for one of its own.
MANAGED_CFG = "home = /opt/uv/python/cpython-3.12/bin\nversion_info = 3.12\ninclude-system-site-packages = false\n"

UV_STUB = """\
#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$UV_LOG"
if [ "$1" = venv ]; then
  mkdir -p .venv
  case " $* " in
    *" --system-site-packages "*) printf 'home = /usr/bin\\nversion_info = %s\\ninclude-system-site-packages = true\\n' "${STUB_PYTHON_VERSION%+}" > .venv/pyvenv.cfg ;;
    *) printf '%s' "$MANAGED_CFG" > .venv/pyvenv.cfg ;;
  esac
fi
"""

# Stands in for /usr/bin/python3: answers the launcher's two questions from STUB_PYTHON_VERSION.
PYTHON_STUB = """\
#!/usr/bin/env bash
case "$2" in
  *version_info*) case "$STUB_PYTHON_VERSION" in 3.[0-9].*) exit 1 ;; *) exit 0 ;; esac ;;
  *python_version*) printf '%s\\n' "$STUB_PYTHON_VERSION" ;;
esac
"""


class Project:
    def __init__(self, root: Path, system_version: str) -> None:
        self.root = root
        self.system_version = system_version
        self.python = root / "bin" / "system-python"
        (root / "scripts").mkdir()
        shutil.copy(LAUNCH, root / "scripts" / "launch.sh")
        (root / "bin").mkdir()
        for path, text in ((root / "bin" / "uv", UV_STUB), (self.python, PYTHON_STUB)):
            path.write_text(text)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @property
    def venv(self) -> Path:
        return self.root / ".venv"

    def system_cfg(self, version: str | None = None) -> str:
        return f"home = /usr/bin\nversion_info = {version or self.system_version}\ninclude-system-site-packages = true\n"

    def launch(self, *args: str) -> list[str]:
        log = self.root / "uv.log"
        log.unlink(missing_ok=True)
        env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "UV_LOG": str(log),
            "MANAGED_CFG": MANAGED_CFG,
            "STUB_PYTHON_VERSION": self.system_version,
            "OPENVPN3_OD_SYSTEM_PYTHON": str(self.python),
        }
        # Started from elsewhere on purpose: the launcher must find its own project directory.
        proc = subprocess.run(["bash", str(self.root / "scripts" / "launch.sh"), *args], cwd="/", env=env, capture_output=True, text=True, timeout=30, check=False)
        assert proc.returncode == 0, proc.stderr
        return log.read_text().splitlines()

    @property
    def venv_call(self) -> str:
        if self.system_version.startswith("3.9."):
            return "venv --python 3.12 --quiet"  # never uv's newest default, which the lock may have no wheels for
        return f"venv --python {self.python} --system-site-packages --quiet"


@pytest.fixture(params=["3.12.3", "3.9.25"], ids=["ubuntu-like", "amazon-linux-2023"])
def project(request, tmp_path) -> Project:
    return Project(tmp_path, request.param)


def test_first_launch_builds_the_venv_for_this_host_then_syncs_and_runs(project):
    assert project.launch("--flag") == [project.venv_call, "sync --quiet", "run --quiet openvpn3-mcp --flag"]
    assert (project.venv / "pyvenv.cfg").is_file()


def test_matching_venv_is_kept(project):
    project.launch()
    (project.venv / "keep-me").write_text("x")
    assert project.launch() == ["sync --quiet", "run --quiet openvpn3-mcp"]
    assert (project.venv / "keep-me").exists()


def test_venv_from_the_other_layout_is_replaced(project):
    project.venv.mkdir()
    # A host whose system Python is too old must not keep the system-site venv the previous launcher built on it, and a
    # host with a usable system Python must not keep a uv-managed one (it can't see python3-dbus / openvpn3).
    (project.venv / "pyvenv.cfg").write_text(project.system_cfg() if project.system_version.startswith("3.9.") else MANAGED_CFG)
    (project.venv / "stale").write_text("x")
    assert project.launch()[0] == project.venv_call
    assert not (project.venv / "stale").exists()


def test_system_venv_from_before_a_python_update_is_replaced(tmp_path):
    project = Project(tmp_path, "3.12.3")
    project.venv.mkdir()
    # The distribution moved python3 on a patch level since the venv was built. Left alone, `uv sync` would replace the
    # venv with one that can't see python3-dbus / openvpn3, and the server would come up without the openvpn3 backend.
    (project.venv / "pyvenv.cfg").write_text(project.system_cfg("3.12.1"))
    (project.venv / "stale").write_text("x")
    assert project.launch()[0] == project.venv_call
    assert not (project.venv / "stale").exists()


def test_system_venv_of_an_unreleased_python_build_is_kept(tmp_path):
    # A distribution snapshot or branch build reports "3.12.0+"; uv records it without the "+". Comparing the two as they
    # come would delete and rebuild a working venv on every start - under a server another session is already running.
    project = Project(tmp_path, "3.12.0+")
    assert project.launch()[0] == project.venv_call
    assert "version_info = 3.12.0\n" in (project.venv / "pyvenv.cfg").read_text()
    (project.venv / "keep-me").write_text("x")
    assert project.launch() == ["sync --quiet", "run --quiet openvpn3-mcp"]
    assert (project.venv / "keep-me").exists()


def test_system_venv_that_records_no_version_is_left_to_uv(tmp_path):
    project = Project(tmp_path, "3.12.3")
    project.venv.mkdir()
    (project.venv / "pyvenv.cfg").write_text("home = /usr/bin\ninclude-system-site-packages = true\n")
    assert project.launch() == ["sync --quiet", "run --quiet openvpn3-mcp"]


@pytest.mark.parametrize("recorded", ["3.12", "3.12.16"])
def test_managed_venv_on_the_pinned_version_is_kept_in_either_spelling(tmp_path, recorded):
    project = Project(tmp_path, "3.9.25")
    project.venv.mkdir()
    (project.venv / "pyvenv.cfg").write_text(MANAGED_CFG.replace("version_info = 3.12", f"version_info = {recorded}"))
    assert project.launch() == ["sync --quiet", "run --quiet openvpn3-mcp"]


@pytest.mark.parametrize("recorded", ["3.15.2", "3.1", "3.120"])
def test_managed_venv_on_another_python_version_is_replaced(tmp_path, recorded):
    project = Project(tmp_path, "3.9.25")
    project.venv.mkdir()
    (project.venv / "pyvenv.cfg").write_text(MANAGED_CFG.replace("version_info = 3.12", f"version_info = {recorded}"))  # e.g. what a bare `uv sync` built
    assert project.launch()[0] == "venv --python 3.12 --quiet"


def test_the_default_system_interpreter_is_usr_bin_python3():
    assert 'system_python="${OPENVPN3_OD_SYSTEM_PYTHON:-/usr/bin/python3}"' in LAUNCH.read_text()
