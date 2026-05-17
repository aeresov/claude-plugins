# openvpn3-mcp

MCP server for `openvpn3-linux`. Talks to its D-Bus services (`net.openvpn.v3.configuration`, `net.openvpn.v3.sessions`) via the `openvpn3` Python module — no CLI shell-out. Ships as part of the `openvpn3-on-demand` Claude Code plugin; not published to PyPI.

Launched by the plugin's `.mcp.json` via `scripts/launch.sh`:

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

Tools: `vpn_status`, `vpn_connect` (BYO), `vpn_connect_ephemeral` (provision-and-connect), `vpn_disconnect`. See the plugin's top-level README for semantics.

## Local dev

```bash
cd servers/openvpn3
# Pin the venv to /usr/bin/python3: --system-site-packages exposes the parent
# interpreter's site-packages, and uv's default managed Python doesn't include
# /usr/lib/python3/dist-packages where openvpn3-client and python3-dbus install.
uv venv --python /usr/bin/python3 --system-site-packages
uv sync
uv run openvpn3-mcp            # stdio server, expects an MCP client
```

## License

AGPL-3.0-only — links the AGPL-licensed `openvpn3` Python module.
