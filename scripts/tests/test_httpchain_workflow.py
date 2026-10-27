# SPDX-License-Identifier: AGPL-3.0-only
"""Offline workflow recipes; set HTTPCHAIN_TEST_PYTHON for package contracts."""

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "plugins/pytest-httpchain/setup-checklist.md"


def recipe(containing):
    blocks = re.findall(r"```bash\n(.*?)\n\s*```", CHECKLIST.read_text(), re.S)
    return textwrap.dedent(next(block for block in blocks if containing in block))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="httpchain-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        (self.project / "pyproject.toml").write_text(
            '[project]\nname = "probe"\nversion = "0.0.0"\n[tool.uv]\n'
        )
        self.environment = os.environ.copy()
        self.environment.pop("PYTHONPATH", None)
        self.environment.pop("PYTHONHOME", None)

    def snapshot(self):
        return {
            str(path.relative_to(self.project)): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
        }

    def run_probe(self, interpreter):
        command = recipe("from importlib.metadata import version").replace(
            "<test-python>", str(interpreter)
        )
        return subprocess.run(
            ["bash", "-c", command], cwd=self.project,
            env=self.environment, capture_output=True, text=True,
        )

    def existing_interpreter(self):
        directory = self.project / ".venv"
        venv.EnvBuilder(with_pip=False).create(directory)
        if os.name == "nt":
            return directory / "Scripts/python.exe", directory / "Lib/site-packages"
        return directory / "bin/python", next(directory.glob("lib/python*/site-packages"))

    def test_missing_environment_does_not_create_venv_or_lockfile(self):
        before = self.snapshot()
        result = self.run_probe(self.project / ".venv/bin/python")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.project / ".venv").exists())
        self.assertFalse((self.project / "uv.lock").exists())

    def test_missing_package_does_not_sync_existing_environment(self):
        interpreter, _ = self.existing_interpreter()
        before = self.snapshot()
        result = self.run_probe(interpreter)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PackageNotFoundError", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_existing_distribution_probe_does_not_modify_project(self):
        interpreter, site_packages = self.existing_interpreter()
        metadata = site_packages / "pytest_httpchain-0.16.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: pytest-httpchain\nVersion: 0.16.0\n"
        )
        before = self.snapshot()
        result = self.run_probe(interpreter)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.16.0")
        self.assertEqual(self.snapshot(), before)


@unittest.skipUnless(os.environ.get("HTTPCHAIN_TEST_PYTHON"), "optional package contract")
class PackageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="httpchain-contract-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / "pytest.ini").write_text("[pytest]\n")
        self.interpreter = os.environ["HTTPCHAIN_TEST_PYTHON"]
        version = subprocess.run(
            [self.interpreter, "-B", "-c",
             "from importlib.metadata import version; print(version('pytest-httpchain'))"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        parts = re.match(r"(\d+)\.(\d+)", version)
        if not parts or tuple(map(int, parts.groups())) < (0, 16):
            self.skipTest("directory/JSONC validation requires pytest-httpchain >=0.16")

    def validate_directory(self):
        command = recipe("validate --format json .").replace(
            "<test-python>", self.interpreter
        )
        result = subprocess.run(
            ["bash", "-c", command], cwd=self.project,
            capture_output=True, text=True,
        )
        return result, json.loads(result.stdout)

    def test_directory_validation_includes_invalid_jsonc_and_excludes_dependencies(self):
        scenario = {"stages": [{
            "name": "get", "request": {"url": "https://example.invalid"},
            "response": [{"verify": {"status": 200}}],
        }]}
        (self.project / "test_good.http.json").write_text(json.dumps(scenario))
        scenario["stages"][0]["response"][0]["verify"]["status"] = "invalid"
        (self.project / "test_bad.http.jsonc").write_text(
            "// JSON with comments\n" + json.dumps(scenario)
        )
        dependencies = self.project / "node_modules"
        dependencies.mkdir()
        (dependencies / "test_ignored.http.json").write_text("invalid")
        result, payload = self.validate_directory()
        self.assertNotEqual(result.returncode, 0)
        files = {Path(item["path"]).name: item["result"] for item in payload["files"]}
        self.assertEqual(set(files), {"test_good.http.json", "test_bad.http.jsonc"})
        self.assertTrue(files["test_good.http.json"]["valid"])
        self.assertFalse(files["test_bad.http.jsonc"]["valid"])

    def test_empty_directory_is_a_distinct_no_scenario_result(self):
        result, payload = self.validate_directory()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(payload["files"]), 1)
        diagnostics = payload["files"][0]["result"]["diagnostics"]
        self.assertEqual([entry["code"] for entry in diagnostics], ["HTTPCHAIN039"])


if __name__ == "__main__":
    unittest.main()
