# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A personal Claude Code plugin **marketplace**. The repo root holds `.claude-plugin/marketplace.json` (consumed by `/plugin marketplace add`); every plugin is self-contained under `plugins/<name>/` with its own `.claude-plugin/plugin.json` — and, where it has one, its own `CLAUDE.md`. **Read `plugins/<name>/CLAUDE.md` before working inside that plugin.**

Plugins:
- [`openvpn3-on-demand`](plugins/openvpn3-on-demand/CLAUDE.md) — Provides MCP tools to bring an OpenVPN3 tunnel up/down on demand.
- [`mysql-client`](plugins/mysql-client/CLAUDE.md) — Read-only-first use of the `mysql` CLI for MySQL/MariaDB investigation, plus a `mysql-investigator` subagent.
- [`pytest-httpchain`](plugins/pytest-httpchain/CLAUDE.md) — Author and validate pytest-httpchain JSON HTTP-API test scenarios; ships the authoring skill, an `httpchain-author` subagent, and `/setup` + `/doctor`.
- [`gitlab-client`](plugins/gitlab-client/CLAUDE.md) — Drive an on-prem GitLab 15.x (repos, merge requests, CI/CD) through a bundled REST client; allow-listed writes, a `pipeline-debugger` subagent, `/setup` + `/doctor`.

## Adding / changing a plugin

Drop it at `plugins/<name>/` with a `.claude-plugin/plugin.json`, then add a matching entry to the `plugins` array in `.claude-plugin/marketplace.json`. The two manifests duplicate `name` / `description` / `version` — keep them in sync (a version bump touches both). `license` lives only in `marketplace.json`. `scripts/check-version-sync.sh` enforces the version part: each `plugin.json` must match its `marketplace.json` entry and any in-tree `pyproject.toml` under that plugin.

## Commands

No build step. The repo-wide gates (mirrored by `.github/workflows/validate.yml`, which runs on every branch and on PRs):

```bash
claude plugin validate .                  # validate marketplace.json
claude plugin validate plugins/<name>     # validate a plugin's plugin.json
scripts/check-version-sync.sh             # plugin.json ↔ marketplace.json ↔ pyproject.toml versions
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
