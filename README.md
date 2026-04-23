# aeresov/claude-plugins

Private Claude Code plugin marketplace.

## Add this marketplace

```bash
# from a local clone
/plugin marketplace add /path/to/this/repo

# or, once pushed to GitHub
/plugin marketplace add aeresov/claude-plugins
```

Then install individual plugins:

```bash
/plugin install openvpn3-on-demand@misc-claude-plugins
```

## Plugins

| Plugin | Description |
|--------|-------------|
| [`openvpn3-on-demand`](plugins/openvpn3-on-demand/) | Raise an OpenVPN3 tunnel on demand before VPN-gated commands; tear it down at session end. |

## Repo layout

```
.claude-plugin/marketplace.json   marketplace manifest (consumed by /plugin marketplace add)
plugins/<name>/                   one directory per plugin; each has its own .claude-plugin/plugin.json
```

To add a new plugin: drop it at `plugins/<name>/` with a `.claude-plugin/plugin.json`, then add an entry under `plugins` in `.claude-plugin/marketplace.json`.
