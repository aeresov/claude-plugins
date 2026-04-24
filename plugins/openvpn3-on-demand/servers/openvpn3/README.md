# openvpn3-mcp

MCP server for `openvpn3-linux`. Talks to the openvpn3 D-Bus services
(`net.openvpn.v3.configuration`, `net.openvpn.v3.sessions`) via the
`openvpn3` Python module shipped with the `openvpn3-client` system
package — no CLI shell-out. Ships as part of the `openvpn3-on-demand`
Claude Code plugin; not published to PyPI.

Launched by `.mcp.json` in the plugin root via `scripts/launch.sh`, which
makes sure the venv has `include-system-site-packages=true` so `import
dbus` and `import openvpn3` resolve to the system-installed copies:

```json
{
  "mcpServers": {
    "openvpn3": {
      "command": "bash",
      "args": ["${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/scripts/launch.sh"]
    }
  }
}
```

Tools: `vpn_status`, `vpn_connect`, `vpn_disconnect`, `vpn_config_import`,
`vpn_config_remove`. See the plugin's top-level README for semantics.

## Local dev

```bash
cd servers/openvpn3
uv venv --system-site-packages  # must have system site-packages for dbus + openvpn3
uv sync
uv run openvpn3-mcp             # runs the stdio server (expects an MCP client)
uv run --group dev pytest -q    # unit tests (stub dbus/openvpn3 — no real D-Bus needed)
```

## Licensing

AGPL-3.0-only. The ported implementation depends on the openvpn3 Python
module, which is AGPL-3.0-only; this server and the whole `claude-plugins`
repo adopt the same license.
