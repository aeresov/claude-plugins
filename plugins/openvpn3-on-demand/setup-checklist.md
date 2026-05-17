# Setup checklist (shared reference)

Read by `/openvpn3-on-demand:setup` and `/openvpn3-on-demand:doctor` via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`. Single source of truth for the 7 checks and their remediation text — `doctor` reports them, `setup` reports then fixes the ones it owns. Lives at the plugin root (not under `commands/`) so it isn't itself a slash command.

Every check is read-only and needs no `sudo`. Run them from the project root.

## Checks

### 1. `openvpn3-client` installed
- Run: `openvpn3 version`
- PASS if it exits 0.
- FAIL → "Install the `openvpn3-client` system package (Debian/Ubuntu: `sudo apt install openvpn3-client`; Fedora/RHEL: enable the upstream OpenVPN 3 repo). It provides the D-Bus services and the `openvpn3` Python module the MCP server uses."

### 2. `python3-dbus` installed
- Run: `python3 -c 'import dbus'`
- PASS if it exits 0.
- FAIL → "Install the `python3-dbus` system package (Debian/Ubuntu: `sudo apt install python3-dbus`). It is not pulled in automatically by `openvpn3-client`; minimal containers need it explicitly."

### 3. Host DNS / netcfg initialized (once per machine)
- Run: `test -f /var/lib/openvpn3/netcfg.json`
- PASS if the file exists.
- FAIL → "Run these once per machine so the tunnel's pushed DNS reaches systemd-resolved (without this, TCP to private IPs works but `*.rds.amazonaws.com` etc. resolve to NXDOMAIN silently):
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```
  Verify with `sudo openvpn3-admin netcfg-service --config-show` — output should include `Systemd-resolved in use: Yes`. Skip on non-systemd hosts (Alpine etc.) — DNS over the tunnel there is out of scope for this plugin."

### 4. Settings file present
- Run: `test -f .claude/openvpn3-on-demand.local.md`
- PASS if it exists.
- FAIL → "Not configured for this project. Run `/openvpn3-on-demand:setup`."

### 5. Settings file valid (only if check 4 passed)
- Parse the YAML frontmatter; look at `profile_name` and `ovpn_provision_cmd`.
- PASS if exactly one is present and non-empty.
- FAIL (both set) → "`profile_name` and `ovpn_provision_cmd` are mutually exclusive. Pick one. Re-run `/openvpn3-on-demand:setup` to fix."
- FAIL (neither set) → "The settings file must declare either `profile_name` (BYO) or `ovpn_provision_cmd` (ephemeral). Re-run `/openvpn3-on-demand:setup` to fix."
- Note the mode for the summary: `profile_name` → BYO; `ovpn_provision_cmd` → ephemeral.

### 6. BYO profile imported (only if check 5 passed AND mode is BYO)
- Run: `openvpn3 configs-list`; look for the configured `profile_name`.
- PASS if a config with that name is listed.
- FAIL → "The openvpn3 config `<profile_name>` isn't imported. Import it once (the plugin never creates or removes a BYO config):
  ```bash
  openvpn3 config-import --config /path/to/your.ovpn --name <profile_name> --persistent
  ```
  The profile must be non-interactive (`auth-user-pass` inlined, no encrypted PKCS#12) — the MCP server can't answer credential prompts."

### 7. `.gitignore` covers the settings file
- Check the project's `.gitignore` for `.claude/*.local.md` (or an exact entry for `.claude/openvpn3-on-demand.local.md`).
- PASS if covered.
- FAIL → "Add `.claude/*.local.md` to `.gitignore` — the settings file can contain internal hostnames and provisioning commands."
