# Setup checklist (shared reference)

Read by `/openvpn3-on-demand:setup` and `/openvpn3-on-demand:doctor` via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`: the single source for the 9 checks and their remediation text. `doctor` reports them; `setup` reports, then fixes the ones it owns. Kept at the plugin root so it isn't a slash command.

All checks are read-only, need no `sudo`, and run from the project root.

## Checks

### 1. `openvpn3-client` installed
- Run: `openvpn3 version` — PASS on exit 0.
- FAIL → "Install the `openvpn3-client` package (Debian/Ubuntu: `sudo apt install openvpn3-client`; Fedora/RHEL: enable the upstream OpenVPN 3 repo). It provides the D-Bus services and the `openvpn3` Python module the MCP server uses."

### 2. `python3-dbus` installed
- Run: `python3 -c 'import dbus'` — PASS on exit 0.
- FAIL → "Install the `python3-dbus` package (Debian/Ubuntu: `sudo apt install python3-dbus`). `openvpn3-client` doesn't pull it in."

### 3. `uv` installed
- Run: `uv --version` — PASS if it prints a version.
- FAIL → "`uv` isn't on `PATH`. It runs the MCP server (`servers/openvpn3/scripts/launch.sh`); without it no `vpn_*` tool exists. Install it (https://docs.astral.sh/uv/), then restart Claude Code."

### 4. Host DNS / netcfg initialized (once per machine)
- Run: `test -f /var/lib/openvpn3/netcfg.json` — PASS if it exists.
- FAIL → "Run once per machine so the tunnel's DNS reaches systemd-resolved (without it, private IPs work but names like `*.rds.amazonaws.com` fail or come back as `0.0.0.0`):
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```
  Verify: `sudo openvpn3-admin netcfg-service --config-show` includes `Systemd-resolved in use: Yes`. Skip on non-systemd hosts (Alpine etc.); DNS over the tunnel there is out of scope."

### 5. Settings file present
- Run: `test -f .claude/openvpn3-on-demand.local.md` — PASS if it exists.
- FAIL → "Not configured for this project. Run `/openvpn3-on-demand:setup`."

### 6. Settings file valid (only if check 5 passed)
- Parse the YAML frontmatter — PASS if exactly one of `profile_name` / `ovpn_provision_cmd` is present and non-empty.
- FAIL (both) → "`profile_name` and `ovpn_provision_cmd` are mutually exclusive — pick one. Re-run `/openvpn3-on-demand:setup` to fix."
- FAIL (neither) → "The settings file must declare `profile_name` (BYO) or `ovpn_provision_cmd` (ephemeral). Re-run `/openvpn3-on-demand:setup` to fix."
- Record the mode for the summary: `profile_name` → BYO, `ovpn_provision_cmd` → ephemeral.

### 7. BYO profile imported (only if check 6 passed AND mode is BYO)
- Run: `openvpn3 configs-list` — PASS if the configured `profile_name` is listed.
- FAIL → "The openvpn3 config `<profile_name>` isn't imported. Import it once (the plugin never creates or removes BYO configs):
  ```bash
  openvpn3 config-import --config /path/to/your.ovpn --name <profile_name> --persistent
  ```
  The profile must be non-interactive (`auth-user-pass` inlined, no encrypted PKCS#12) — the MCP server can't answer credential prompts."

### 8. `.gitignore` covers the settings file
- PASS if the project's `.gitignore` has `.claude/*.local.md` or `.claude/openvpn3-on-demand.local.md`.
- FAIL → "Add `.claude/*.local.md` to `.gitignore` — the settings file can hold internal hostnames and provisioning commands."

### 9. Programs resolve through systemd-resolved (once per machine)
- Run: `python3 "<plugin root>/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" [<hostname>]` (`doctor` and `setup` give the full path). Pass a private hostname when one is known; with the tunnel up it adds a comparison of both lookup paths. Without one it only reads the resolver setup.
- PASS on exit 0 (including its `SKIP:` on hosts without systemd-resolved), WARN on 3, FAIL on 1. Exit 2 = hostname rejected: report it, rerun without one.
- WARN / FAIL → print the output as-is: what owns `/etc/resolv.conf`, what programs got, and the root-only fix for the user. Check 4 gets the tunnel's DNS *into* systemd-resolved; this checks that programs *ask* it. If Tailscale, a VPN client or NetworkManager owns `/etc/resolv.conf` and nsswitch has no `resolve`, private names fail or return `0.0.0.0` (clients then hit this machine or hang) while check 4 and `resolvectl query` look fine.
