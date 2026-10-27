# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise shared shell recipes without database, VPN or network access."""

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_marketplaces", ROOT / "scripts/check-marketplaces.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in (".agents/plugins", ".claude-plugin"):
            shutil.copytree(ROOT / directory, self.root / directory)
        for plugin in (ROOT / "plugins").glob("*/plugin.json"):
            shutil.copytree(plugin.parent, self.root / "plugins" / plugin.parent.name, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache"))

    def catalog(self):
        return self.root / ".agents/plugins/marketplace.json"

    def change_catalog(self, edit):
        path = self.catalog()
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))

    def test_shared_packages_are_valid_and_deprecated_plugin_is_excluded(self):
        CHECKER.check(self.root)
        names = {p["name"] for p in json.loads(self.catalog().read_text())["plugins"]}
        self.assertEqual(names, {"mysql-client", "pytest-httpchain", "openvpn3-on-demand"})

    def test_metadata_drift_is_rejected(self):
        self.change_catalog(lambda data: data["plugins"][0].update(version="99.0.0"))
        with self.assertRaisesRegex(ValueError, "version differs"):
            CHECKER.check(self.root)

    def test_missing_catalog_entry_is_rejected(self):
        self.change_catalog(lambda data: data["plugins"].pop())
        with self.assertRaisesRegex(ValueError, "exactly the plugins"):
            CHECKER.check(self.root)

    def test_source_cannot_escape_marketplace(self):
        self.change_catalog(lambda data: data["plugins"][0]["source"].update(path="./../outside"))
        with self.assertRaisesRegex(ValueError, "escaping package path"):
            CHECKER.check(self.root)

    def test_missing_procedure_is_rejected(self):
        (self.root / "plugins/mysql-client/skills/mysql-client/references/investigation.md").unlink()
        with self.assertRaisesRegex(ValueError, "missing or escaping reference"):
            CHECKER.check(self.root)

    def test_missing_portable_mcp_manifest_is_rejected(self):
        (self.root / "plugins/openvpn3-on-demand/mcp.json").unlink()
        with self.assertRaisesRegex(ValueError, "MCP manifests must be present for both clients"):
            CHECKER.check(self.root)

    def test_missing_claude_mcp_manifest_is_rejected(self):
        (self.root / "plugins/openvpn3-on-demand/.mcp.json").unlink()
        with self.assertRaisesRegex(ValueError, "MCP manifests must be present for both clients"):
            CHECKER.check(self.root)


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.env = os.environ.copy()
        for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_PLUGIN_ROOT"):
            self.env.pop(key, None)
        self.env["XDG_RUNTIME_DIR"] = str(self.directory)

    def recipe(self, plugin, skill, placeholder, command):
        text = (ROOT / "plugins" / plugin / "skills" / skill / "SKILL.md").read_text()
        block = next(block for block in re.findall(r"```bash\n(.*?)\n\s*```", text, re.S) if placeholder in block)
        return textwrap.dedent(block).replace(placeholder, command).replace("<plugin-root>", str(ROOT / "plugins" / plugin))

    def run_recipe(self, recipe):
        return subprocess.run(["bash", "-c", recipe], cwd=self.directory, env=self.env, capture_output=True, text=True)

    def mysql_recipe(self, command):
        return self.recipe("mysql-client", "mysql-client", "<connection_cmd>", command)

    def test_mysql_files_are_private_distinct_and_do_not_print_credentials(self):
        recipe = self.mysql_recipe("printf '%s' 'mysql://reader:test-password@localhost/demo'")
        results = [self.run_recipe(recipe), self.run_recipe(recipe)]
        paths = []
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("test-password", result.stdout + result.stderr)
            path = Path(result.stdout.strip().removeprefix("cnf="))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            paths.append(path)
        self.assertNotEqual(*paths)
        paths[0].unlink()
        self.assertTrue(paths[1].is_file())

    def test_mysql_failed_provisioning_removes_its_files(self):
        result = self.run_recipe(self.mysql_recipe("printf '%s' 'not-a-url'"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.directory / "mysql-client").iterdir()), [])

    def test_vpn_fallback_identifiers_are_unique_and_private(self):
        recipe = self.recipe("openvpn3-on-demand", "vpn-on-demand", "<provision-cmd>", "printf '%s' 'test-vpn-config'")
        identifiers = []
        for _ in range(2):
            result = self.run_recipe(recipe)
            self.assertEqual(result.returncode, 0, result.stderr)
            fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
            identifiers.append(uuid.UUID(fields["task_id"]))
            self.assertNotIn("test-vpn-config", result.stdout + result.stderr)
            self.assertEqual(stat.S_IMODE(Path(fields["ovpn"]).stat().st_mode), 0o600)
        self.assertNotEqual(*identifiers)

    def test_vpn_preserves_either_client_identity(self):
        recipe = self.recipe("openvpn3-on-demand", "vpn-on-demand", "<provision-cmd>", "printf '%s' 'test-vpn-config'")
        for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
            with self.subTest(client=key):
                self.env[key] = "test-thread-123"
                result = self.run_recipe(recipe)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("task_id=test-thread-123\n", result.stdout)
                self.env.pop(key)


if __name__ == "__main__":
    unittest.main()
