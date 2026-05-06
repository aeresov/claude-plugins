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
# Pin to /usr/bin/python3 explicitly: --system-site-packages only exposes
# the *parent interpreter's* site-packages, and uv's default Python is its
# own managed interpreter (under ~/.local/share/uv/python/), whose
# site-packages does NOT include /usr/lib/python3/dist-packages where
# openvpn3-client and python3-dbus install dbus + openvpn3.
uv venv --python /usr/bin/python3 --system-site-packages
uv sync
uv run openvpn3-mcp             # runs the stdio server (expects an MCP client)
uv run --group dev pytest -q    # unit tests (stub dbus/openvpn3 — no real D-Bus needed)
```

## Licensing

AGPL-3.0-only. The ported implementation depends on the openvpn3 Python
module, which is AGPL-3.0-only; this server and the whole `claude-plugins`
repo adopt the same license.
