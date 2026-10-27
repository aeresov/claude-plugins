#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Check the shared identity and package paths of dual-client plugins."""

import json
import re
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def contained(root: Path, path: str) -> Path:
    if not path.startswith("./"):
        raise ValueError(f"{root}: package path must start with ./: {path}")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.exists():
        raise ValueError(f"{root}: missing or escaping package path: {path}")
    return target


def entries(catalog: dict) -> dict:
    result = {}
    for entry in catalog["plugins"]:
        name = entry["name"]
        if name in result:
            raise ValueError(f"duplicate plugin entry: {name}")
        result[name] = entry
    return result


def check(root: Path) -> None:
    claude = entries(load(root / ".claude-plugin/marketplace.json"))
    codex = entries(load(root / ".agents/plugins/marketplace.json"))
    portable_names = {load(p)["name"] for p in (root / "plugins").glob("*/plugin.json")}
    if set(codex) != portable_names:
        raise ValueError("Codex catalog must list exactly the plugins with portable manifests")
    for name, entry in codex.items():
        source = entry["source"]
        if source["source"] != "local":
            raise ValueError(f"{name}: expected a local source in this repository")
        plugin = contained(root, source["path"])
        claude_plugin = contained(root, claude[name]["source"])
        if plugin != claude_plugin:
            raise ValueError(f"{name}: clients must point to the same plugin directory")
        portable = load(plugin / "plugin.json")
        legacy = load(plugin / ".claude-plugin/plugin.json")
        for key in ("name", "description", "version"):
            values = [manifest[key] for manifest in (entry, claude[name], portable, legacy)]
            if len(set(values)) != 1:
                raise ValueError(f"{name}: {key} differs between catalogs/manifests")
        if portable["$schema"] != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
            raise ValueError(f"{name}: missing portable plugin schema")
        if portable["license"] != claude[name]["license"] or entry["license"] != portable["license"]:
            raise ValueError(f"{name}: licenses differ")
        if entry["policy"] != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"} or not entry["category"]:
            raise ValueError(f"{name}: missing marketplace policy/category")
        extension = portable["extensions"]["com.openai"]
        if not extension["requires_local_executor"] or not extension["interface"]["displayName"]:
            raise ValueError(f"{name}: missing local-execution/listing metadata")
        onboarding = contained(plugin, extension["onboardingSkill"])
        skills = list((plugin / "skills").glob("*/SKILL.md"))
        if onboarding not in [p.resolve() for p in skills]:
            raise ValueError(f"{name}: onboarding must point to a packaged skill")
        for skill in skills:
            text = skill.read_text()
            frontmatter = text.split("---", 2)[1]
            match = re.search(r"^name: ([a-z0-9-]+)$", frontmatter, re.M)
            if not match or match[1] != skill.parent.name or not re.search(r"^description: .+", frontmatter, re.M):
                raise ValueError(f"{skill}: missing or inconsistent skill frontmatter")
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if "://" not in link and not link.startswith("#"):
                    target = (skill.parent / link.split("#", 1)[0]).resolve()
                    if not target.is_relative_to(plugin) or not target.exists():
                        raise ValueError(f"{skill}: missing or escaping reference: {link}")
        for verb in ("setup", "doctor"):
            wrapper = (plugin / "commands" / f"{verb}.md").read_text()
            if f"/skills/{name}-{verb}/SKILL.md" not in wrapper:
                raise ValueError(f"{name}: {verb} must wrap the shared skill")
        if (plugin / "mcp.json").exists():
            portable_mcp = load(plugin / "mcp.json")["mcpServers"]
            legacy_mcp = load(plugin / ".mcp.json")["mcpServers"]
            if set(portable_mcp) != set(legacy_mcp):
                raise ValueError(f"{name}: MCP server names differ")
            for server, config in portable_mcp.items():
                legacy_config = legacy_mcp[server]
                if config["type"] != "stdio" or config["command"] != legacy_config["command"]:
                    raise ValueError(f"{name}: MCP launch commands differ")
                expanded = [arg.replace("${PLUGIN_ROOT}", str(plugin)) for arg in config["args"]]
                legacy_expanded = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin)) for arg in legacy_config["args"]]
                if expanded != legacy_expanded or not Path(expanded[0]).is_file():
                    raise ValueError(f"{name}: MCP launch paths differ or are missing")
    print(f"OK — {len(codex)} dual-client plugins; catalog metadata, skills and MCP paths agree")


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    except (KeyError, IndexError, OSError, ValueError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
