# aeresov/claude-plugins

Personal plugin marketplaces for Claude Code and Codex. The maintained plugins share their skills, references and implementation across both clients.

## Claude Code

```bash
# from a local clone
/plugin marketplace add /path/to/this/repo

# or, from GitHub
/plugin marketplace add aeresov/claude-plugins
```

Then install individual plugins:

```bash
/plugin install openvpn3-on-demand@aeresov-claude-plugins
```

## Codex

From a local clone:

```bash
codex plugin marketplace add /absolute/path/to/claude-plugins
codex plugin list --available --marketplace aeresov-codex-plugins
codex plugin add pytest-httpchain@aeresov-codex-plugins
codex plugin add mysql-client@aeresov-codex-plugins
codex plugin add openvpn3-on-demand@aeresov-codex-plugins
```

Or register the GitHub source with `codex plugin marketplace add aeresov/claude-plugins`. Codex uses `.agents/plugins/marketplace.json`; Claude uses `.claude-plugin/marketplace.json`. The Codex catalog includes only the three maintained plugins. GitLab remains available only in the existing Claude catalog.

After installation, start a new chat or restart the client if the skills have not appeared. Invoke the skills with `$pytest-httpchain:pytest-httpchain`, `$mysql-client:mysql-client`, or `$openvpn3-on-demand:vpn-on-demand`, or describe a matching task. Setup and health checks are `$<plugin-name>:<plugin-name>-setup` and `$<plugin-name>:<plugin-name>-doctor`, for example `$mysql-client:mysql-client-setup`. These are skill mentions, not shell commands. The existing Claude `/plugin-name:setup` and `/plugin-name:doctor` commands wrap those same workflows.

Both clients intentionally read the same gitignored `.claude/*.local.md` project settings. Plugin helpers are resolved relative to the installed skill instead of relying on Claude shell variables. MySQL credentials use unique temporary files; ephemeral VPN profiles use a client task identifier or a generated UUID. Normal completion cleans up the exact files/profile owned by the workflow; interruption can leave files or a tunnel for manual cleanup.

These plugins need local execution. MySQL needs the `mysql` client; pytest-httpchain needs the package in the consuming project's test environment; OpenVPN additionally needs Linux, OpenVPN3 and D-Bus on the execution host. Installation does not supply host packages or a cloud network route to your VPN. See each plugin's README for prerequisites and boundaries.

## Plugins

| Plugin | Description |
|--------|-------------|
| [`openvpn3-on-demand`](plugins/openvpn3-on-demand/) | Provides MCP tools to bring an OpenVPN3 tunnel up/down on demand. |
| [`mysql-client`](plugins/mysql-client/) | Read-only-first use of the `mysql` CLI for MySQL/MariaDB investigation, plus a `mysql-investigator` subagent. |
| [`pytest-httpchain`](plugins/pytest-httpchain/) | Author and validate pytest-httpchain JSON HTTP-API test scenarios; ships the authoring skill, an `httpchain-author` subagent, and `/setup` + `/doctor`. |
| [`gitlab-client`](plugins/gitlab-client/) | **Deprecated, no longer maintained.** Drive an on-prem GitLab 15.x (repos, merge requests, CI/CD) through a bundled REST client; allow-listed writes, a `pipeline-debugger` subagent, `/setup` + `/doctor`. |

## Repo layout

```text
.claude-plugin/marketplace.json    Claude catalog (includes deprecated GitLab)
.agents/plugins/marketplace.json   Codex catalog (maintained plugins only)
plugins/<name>/
  .claude-plugin/plugin.json      Claude manifest
  plugin.json                     Portable manifest for Codex
  skills/                         Shared workflows and references
  commands/                       Thin Claude setup/doctor wrappers
  agents/                         Thin Claude agent wrappers, where used
  scripts/ or servers/            Shared implementation, where needed
```

OpenVPN ships `.mcp.json` for Claude and portable `mcp.json` for Codex; both launch the same server. Its launcher retains the system Python / D-Bus bootstrap. Claude registers named agents; Codex can delegate the shared procedures through its available subagents, or follow them inline.

Keep `name`, `description` and `version` synchronized in both catalogs and both manifests for each maintained plugin. `scripts/check-version-sync.sh` checks those fields, Python project versions, onboarding skills, reference paths and MCP launch parity. CI also runs the checker regression tests and each Python project's `make check`.

## License

AGPL-3.0-only for the whole repo — forced by `openvpn3-on-demand` linking the AGPL `openvpn3` Python module. See [LICENSE](LICENSE).
