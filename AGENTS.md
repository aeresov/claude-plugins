# Maintainer guide

Shared repository guidance for Claude Code and Codex. Read a plugin's `CLAUDE.md` before changing files inside it.

## What this repo is

A personal repository with Claude Code and Codex plugin **marketplaces**. The repo root holds `.claude-plugin/marketplace.json` for Claude and `.agents/plugins/marketplace.json` for Codex. Every plugin is self-contained under `plugins/<name>/`. Maintained plugins have a Claude `.claude-plugin/plugin.json` and a portable root `plugin.json` for Codex, sharing `skills/`, references, helpers and servers. GitLab is deprecated and remains Claude-only. **Read `plugins/<name>/CLAUDE.md` before working inside that plugin.**

Plugins:
- [`openvpn3-on-demand`](plugins/openvpn3-on-demand/CLAUDE.md) — Provides MCP tools to bring an OpenVPN3 tunnel up/down on demand.
- [`mysql-client`](plugins/mysql-client/CLAUDE.md) — Read-only-first use of the `mysql` CLI for MySQL/MariaDB investigation, plus a `mysql-investigator` subagent.
- [`pytest-httpchain`](plugins/pytest-httpchain/CLAUDE.md) — Author and validate pytest-httpchain JSON HTTP-API test scenarios; ships the authoring skill, an `httpchain-author` subagent, and `/setup` + `/doctor`.
- [`gitlab-client`](plugins/gitlab-client/CLAUDE.md) — **Deprecated; don't work on it.** Drive an on-prem GitLab 15.x (repos, merge requests, CI/CD) through a bundled REST client; allow-listed writes, a `pipeline-debugger` subagent, `/setup` + `/doctor`.

## Adding / changing a plugin

For maintained plugins, update both manifests and both catalogs together. `name`, `description` and `version` must agree; the portable manifest also carries the license. Python project versions track their plugin, with lockfiles refreshed after a bump. `scripts/check-version-sync.sh` enforces those identities, onboarding/reference paths and the parity of MCP launch files.

Shared setup/doctor instructions live in `skills/<plugin-name>-setup/SKILL.md` and `skills/<plugin-name>-doctor/SKILL.md`. Claude's `commands/*.md` are thin wrappers retaining Claude-specific permissions. Shared multi-step procedures live beside the main skill in `references/`; Claude's `agents/*.md` wrap them, and Codex delegates them when available or runs them inline. Keep host-specific model/tool names out of shared procedures.

Both clients deliberately reuse gitignored `.claude/*.local.md` settings. Resolve helper paths from the installed skill's location, never the current project's plugin clone or an assumed shell variable. Retain unique temporary-file paths explicitly across shell calls and clean up only the workflow's own files.

## Commands

No build step. The repo-wide gates (mirrored by `.github/workflows/validate.yml`, which runs on every branch and on PRs):

```bash
claude plugin validate .                  # validate marketplace.json
claude plugin validate plugins/<name>     # validate a plugin's plugin.json
scripts/check-version-sync.sh             # both catalogs/manifests + pyproject versions + package paths
python3 -m unittest discover -s scripts/tests  # checker regressions
```

Every in-tree Python project (`pyproject.toml` with a sibling `tests/`) carries the **same `Makefile`**, so the per-project workflow is uniform:

```bash
make sync     # install the dev group
make test     # pytest -q
make tidyup   # ruff autofix + format
make check    # ruff + ruff format --check + ty + pytest — exactly what CI runs
make update   # uv lock --upgrade && uv sync
```

CI discovers those projects automatically, runs `uv sync --locked --group dev` and then **`make check` itself** in each — so the local gate and CI are the same command and cannot drift. A project with `tests/` but no `Makefile` fails the build. Plugin-specific commands (running a server, a live smoke test) live in that plugin's `CLAUDE.md`.

## Repo-wide conventions

**License: AGPL-3.0-only** for the whole repo — forced by `openvpn3-on-demand` linking the AGPL `openvpn3` Python module (see its `CLAUDE.md` for the details). New Python/shell source files start with `# SPDX-License-Identifier: AGPL-3.0-only` after any shebang; `LICENSE` (repo root) must travel with redistributions.
