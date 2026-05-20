# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A personal Claude Code plugin **marketplace**. The repo root holds `.claude-plugin/marketplace.json` (consumed by `/plugin marketplace add`); every plugin is self-contained under `plugins/<name>/` with its own `.claude-plugin/plugin.json` — and, where it has one, its own `CLAUDE.md`. **Read `plugins/<name>/CLAUDE.md` before working inside that plugin.**

Plugins:
- [`openvpn3-on-demand`](plugins/openvpn3-on-demand/CLAUDE.md) — Provides MCP tools to bring an OpenVPN3 tunnel up/down on demand.
- [`mysql-client`](plugins/mysql-client/CLAUDE.md) — Read-only-first use of the `mysql` CLI for MySQL/MariaDB investigation, plus a `mysql-investigator` subagent.

## Adding / changing a plugin

Drop it at `plugins/<name>/` with a `.claude-plugin/plugin.json`, then add a matching entry to the `plugins` array in `.claude-plugin/marketplace.json`. The two manifests duplicate `name` / `description` / `version` / `license` — keep them in sync (a version bump touches both).

## Commands

No build or lint step. The gates (mirrored by `.github/workflows/validate.yml` on push/PR):

```bash
claude plugin validate .                  # validate marketplace.json
claude plugin validate plugins/<name>     # validate a plugin's plugin.json
```

CI additionally runs every in-tree `plugins/*/servers/*/` Python test suite via `uv`. Per-plugin test/run commands live in that plugin's `CLAUDE.md`.

## Repo-wide conventions

**License: AGPL-3.0-only** for the whole repo — forced by `openvpn3-on-demand` linking the AGPL `openvpn3` Python module (see its `CLAUDE.md` for the details). New Python/shell source files start with `# SPDX-License-Identifier: AGPL-3.0-only` after any shebang; `LICENSE` (repo root) must travel with redistributions.
