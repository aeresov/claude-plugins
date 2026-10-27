# openvpn3-mcp

MCP server for `openvpn3-linux`. Talks to its D-Bus services (`net.openvpn.v3.configuration`, `net.openvpn.v3.sessions`) via the `openvpn3` Python module — no CLI shell-out. Ships as part of the `openvpn3-on-demand` Claude Code and Codex plugin; not published to PyPI.

Launched by Claude's `.mcp.json` or Codex's portable `mcp.json` via the same `scripts/launch.sh`. The Claude wiring is:

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

Codex uses `${PLUGIN_ROOT}` in its portable configuration instead of `${CLAUDE_PLUGIN_ROOT}`. The launcher is identical and keeps its system-Python/D-Bus bootstrap.

Tools: `vpn_status`, `vpn_connect` (BYO), `vpn_connect_ephemeral` (provision-and-connect), `vpn_disconnect`. See the plugin's top-level README for semantics.

## Local dev

```bash
cd servers/openvpn3
# Pin to /usr/bin/python3 with --system-site-packages: uv's managed Python can't
# see /usr/lib/python3/dist-packages where openvpn3-client and python3-dbus live.
uv venv --python /usr/bin/python3 --system-site-packages
uv sync
uv run openvpn3-mcp            # stdio server, expects an MCP client
```

## License

AGPL-3.0-only — links the AGPL-licensed `openvpn3` Python module.
