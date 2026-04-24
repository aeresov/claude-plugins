# openvpn3-mcp

MCP server that wraps the `openvpn3-linux` CLI. Ships as part of the
`openvpn3-on-demand` Claude Code plugin; not published to PyPI.

Launched by `.mcp.json` in the plugin root:

```json
{
  "mcpServers": {
    "openvpn3": {
      "command": "uv",
      "args": [
        "run", "--quiet",
        "--project", "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3",
        "openvpn3-mcp"
      ]
    }
  }
}
```

Tools: `vpn_status`, `vpn_connect`, `vpn_disconnect`, `vpn_config_import`,
`vpn_config_remove`. See the plugin's top-level README for semantics.

## Local dev

```bash
cd servers/openvpn3
uv sync
uv run openvpn3-mcp         # runs the stdio server (expects an MCP client)
```
