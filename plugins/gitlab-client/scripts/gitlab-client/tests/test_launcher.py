# SPDX-License-Identifier: AGPL-3.0-only
"""The bash launcher must import the bundled package, not whatever the current directory holds."""
import os
import subprocess
from pathlib import Path

GL = Path(__file__).resolve().parents[2] / "gl"


def run(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLAB_CLIENT_")}
    env.pop("PYTHONPATH", None)
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
