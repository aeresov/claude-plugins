# openvpn-mcp

MCP server that brings a VPN tunnel up and down. On hosts with `openvpn3-linux` it talks to its D-Bus services (`net.openvpn.v3.configuration`, `net.openvpn.v3.sessions`) via the `openvpn3` Python module — no CLI shell-out. On hosts without it (Amazon Linux 2023) it runs the distribution's OpenVPN 2.6 in a transient systemd unit, through the root-side helper `src/openvpn_mcp/ovpn2_helper.py`. Ships as part of the `openvpn-on-demand` Claude Code and Codex plugin; not published to PyPI.

Launched by Claude's `.mcp.json` or Codex's portable `mcp.json` via the same `scripts/launch.sh`. The Claude wiring is:

```json
{
  "mcpServers": {
    "openvpn": {
      "command": "bash",
      "args": ["${CLAUDE_PLUGIN_ROOT}/servers/openvpn/scripts/launch.sh"]
    }
  }
}
```

Codex uses `${PLUGIN_ROOT}` in its portable configuration instead of `${CLAUDE_PLUGIN_ROOT}`. The launcher is identical: it builds the venv on the system Python (for D-Bus and the `openvpn3` module), or on a uv-managed Python where the system one is older than 3.10.

Tools: `vpn_status`, `vpn_connect` (BYO), `vpn_connect_ephemeral` (provision-and-connect), `vpn_disconnect`. See the plugin's top-level README for semantics.

## Local dev

```bash
cd servers/openvpn
# Pin to /usr/bin/python3 with --system-site-packages: uv's managed Python can't
# see /usr/lib/python3/dist-packages where openvpn3-client and python3-dbus live.
uv venv --python /usr/bin/python3 --system-site-packages
uv sync
uv run openvpn-mcp            # stdio server, expects an MCP client
```

Where `/usr/bin/python3` is older than 3.10 use `uv venv --python 3.12` instead; that venv serves the OpenVPN 2 backend only. `scripts/launch.sh` makes this choice by itself.

## License

AGPL-3.0-only — links the AGPL-licensed `openvpn3` Python module.
