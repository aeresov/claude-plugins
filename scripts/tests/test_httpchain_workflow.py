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
PLUGIN = ROOT / "plugins/pytest-httpchain"
CHECKLIST = PLUGIN / "setup-checklist.md"
SKILL = PLUGIN / "skills/pytest-httpchain"
SETUP = PLUGIN / "skills/pytest-httpchain-setup/SKILL.md"
VERSION_PROBE = "from importlib.metadata import version; print(version('pytest-httpchain'))"


def recipe(containing):
    blocks = re.findall(r"```bash\n(.*?)\n\s*```", CHECKLIST.read_text(), re.S)
    return textwrap.dedent(next(block for block in blocks if containing in block))


def installed_version(interpreter):
    probe = subprocess.run(
        [interpreter, "-B", "-c", VERSION_PROBE], capture_output=True, text=True, check=True,
    )
    parts = re.match(r"(\d+)\.(\d+)", probe.stdout.strip())
    return tuple(map(int, parts.groups())) if parts else (0, 0)


def documented_version():
    """The package release the authoring skill says it documents."""
    marker = re.search(r"documents pytest-httpchain \*\*(\d+)\.(\d+)", (SKILL / "SKILL.md").read_text())
    return tuple(map(int, marker.groups()))


def documented_scenarios():
    """Each fenced JSON block that is a whole scenario, as (label, text).

    A fragment (a stage, a request, a block with `{ ... }` placeholders) is no
    scenario, and one composed from other files cannot be validated alone.
    """
    for path in [*sorted(SKILL.rglob("*.md")), SETUP]:
        blocks = re.findall(r"```json\n(.*?)\n\s*```", path.read_text(), re.S)
        for index, block in enumerate(blocks):
            text = textwrap.dedent(block)
            try:
                scenario = json.loads(text)
            except ValueError:
                continue
            composed = any(key in text for key in ('"$include"', '"$merge"', '"$ref"'))
            if isinstance(scenario, dict) and scenario.get("stages") and not composed:
                yield f"{path.parent.name}_{path.stem}_{index}".replace("-", "_").lower(), text


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
        if installed_version(self.interpreter) < (0, 16):
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


@unittest.skipUnless(os.environ.get("HTTPCHAIN_TEST_PYTHON"), "optional package contract")
class DocumentationContractTests(unittest.TestCase):
    """The authoring reference against the package release it documents."""

    def setUp(self):
        self.interpreter = os.environ["HTTPCHAIN_TEST_PYTHON"]
        if installed_version(self.interpreter) < documented_version():
            self.skipTest("the authoring reference documents a newer pytest-httpchain")

    def package(self, code):
        result = subprocess.run(
            [self.interpreter, "-B", "-c", code], capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_documented_scenarios_validate_without_findings(self):
        scenarios = dict(documented_scenarios())
        self.assertGreaterEqual(len(scenarios), 8, "too few whole scenarios found in the reference")
        with tempfile.TemporaryDirectory(prefix="httpchain-docs-") as temp:
            project = Path(temp)
            (project / "pytest.ini").write_text("[pytest]\n")
            for label, text in scenarios.items():
                (project / f"test_{label}.http.json").write_text(text)
            result = subprocess.run(
                [self.interpreter, "-B", "-m", "pytest_httpchain.cli", "validate", "--format", "json", "."],
                cwd=project, capture_output=True, text=True,
            )
            payload = json.loads(result.stdout)
        findings = {
            Path(item["path"]).name: [
                f"{entry['severity']} [{entry['code']}]: {entry['message']}"
                for entry in item["result"]["diagnostics"] if entry["severity"] != "info"
            ]
            for item in payload["files"]
        }
        self.assertEqual(len(findings), len(scenarios))
        self.assertEqual({name: found for name, found in findings.items() if found}, {})

    def test_diagnostics_reference_matches_package_severities(self):
        documented = dict(re.findall(
            r"^\| `(HTTPCHAIN\d{3})` \| (error|warning|info) \|",
            (SKILL / "references/diagnostics.md").read_text(), re.M,
        ))
        package = self.package(
            "import json; from pytest_httpchain.validation.diagnostics import SEVERITY; "
            "print(json.dumps({code.value: severity for code, severity in SEVERITY.items()}))"
        )
        self.assertEqual(documented, package)

    def test_template_builtins_are_documented(self):
        reference = (SKILL / "references/templates.md").read_text()
        package = self.package(
            "import json; from pytest_httpchain.templates.substitution import TEMPLATE_BUILTINS; "
            "print(json.dumps(sorted(TEMPLATE_BUILTINS)))"
        )
        missing = [name for name in package if not re.search(rf"`{name}(?:`|\()", reference)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
