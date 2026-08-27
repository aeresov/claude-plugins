# aeresov/claude-plugins

Personal Claude Code plugin marketplace.

## Add this marketplace

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

## Plugins

| Plugin | Description |
|--------|-------------|
| [`openvpn3-on-demand`](plugins/openvpn3-on-demand/) | Provides MCP tools to bring an OpenVPN3 tunnel up/down on demand. |
| [`mysql-client`](plugins/mysql-client/) | Read-only-first use of the `mysql` CLI for MySQL/MariaDB investigation, plus a `mysql-investigator` subagent. |
| [`pytest-httpchain`](plugins/pytest-httpchain/) | Author and validate pytest-httpchain JSON HTTP-API test scenarios; ships the authoring skill, an `httpchain-author` subagent, and `/setup` + `/doctor`. |
| [`gitlab-client`](plugins/gitlab-client/) | Drive an on-prem GitLab 15.x (repos, merge requests, CI/CD) through a bundled REST client; allow-listed writes, a `pipeline-debugger` subagent, `/setup` + `/doctor`. |

## Repo layout

```
.claude-plugin/marketplace.json   marketplace manifest (consumed by /plugin marketplace add)
plugins/<name>/                   one directory per plugin; each has its own .claude-plugin/plugin.json
```

To add a new plugin: drop it at `plugins/<name>/` with a `.claude-plugin/plugin.json`, then add an entry under `plugins` in `.claude-plugin/marketplace.json`.

## License

AGPL-3.0-only for the whole repo — forced by `openvpn3-on-demand` linking the AGPL `openvpn3` Python module. See [LICENSE](LICENSE).
