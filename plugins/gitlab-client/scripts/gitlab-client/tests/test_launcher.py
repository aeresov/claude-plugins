# SPDX-License-Identifier: AGPL-3.0-only
"""The bash launcher must import the bundled package, not whatever the current directory holds."""

import os
import shutil
import subprocess
from pathlib import Path

GL = Path(__file__).resolve().parents[2] / "gl"


def run(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLAB_CLIENT_")}
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(cwd)  # else the launcher reads the developer's own ~/.claude/gitlab-client.local.md
    return subprocess.run([str(GL), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30)


def test_launcher_ignores_decoy_package_in_cwd(tmp_path):
    decoy = tmp_path / "gitlab_client"
    decoy.mkdir()
    (decoy / "__init__.py").write_text("raise SystemExit('hijacked')\n")
    (decoy / "cli.py").write_text("raise SystemExit('hijacked')\n")
    proc = run(tmp_path, "--version")
    assert proc.returncode == 0 and proc.stdout.strip().startswith("gl ") and "hijacked" not in proc.stderr


def test_launcher_exit_codes(tmp_path):
    proc = run(tmp_path, "api", "GET", "/user")
    assert proc.returncode == 2 and "no GitLab URL configured" in proc.stderr
    proc = run(tmp_path, "api", "PUT", "/projects/:project/merge_requests/1/merge")
    assert proc.returncode == 3 and "write policy" in proc.stderr


def test_launcher_without_git_names_the_way_forward(tmp_path):
    # Bare Amazon Linux 2023: no git on PATH. Only what the launcher itself needs is linked in.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "dirname", "python3"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path), "GITLAB_CLIENT_URL": "https://gitlab.invalid", "GITLAB_CLIENT_TOKEN": "x"}
    proc = subprocess.run([str(GL), "project"], cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert "git isn't installed" in proc.stderr and "run from the project root" in proc.stderr
