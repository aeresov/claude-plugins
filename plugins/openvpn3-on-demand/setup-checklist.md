# openvpn3-on-demand — setup checklist (shared reference)

This file is read (via `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`) by `/openvpn3-on-demand:setup`
and `/openvpn3-on-demand:doctor`. It is the single source of truth for "what does a correct
install look like" — both commands run the checks below; `doctor` only reports, `setup` reports
then fixes the parts it owns. (It lives at the plugin root, not under `commands/`, so it isn't
itself registered as a slash command.)

Every check is read-only and needs no `sudo`. Run them from the project root.

## Standing facts (state these where relevant)

- The settings file lives at `.claude/openvpn3-on-demand.local.md` in the **project root**.
  It is user-owned and must be git-ignored.
- Edits to that file take effect immediately — the skill re-reads it every turn and the
  Stop/SessionEnd teardown hook re-reads it every fire. **No Claude Code restart is needed**
  for settings changes. Only edits to the plugin's own `.mcp.json` / `hooks.json` need a
  restart (those load at session start).
- Exactly one of `profile_name` (BYO mode — a config you imported yourself) or
  `ovpn_provision_cmd` (ephemeral mode — a command whose **stdout** is the `.ovpn` body)
  must be present and non-empty. Both, or neither, is a configuration error and the skill
  skips the VPN entirely.

## Checks

### 1. `openvpn3-client` installed
- Run: `openvpn3 version`
- PASS if it exits 0.
- FAIL → "Install the `openvpn3-client` system package (Debian/Ubuntu: `sudo apt install openvpn3-client`; Fedora/RHEL: enable the upstream OpenVPN 3 repo). It provides the D-Bus services and the `openvpn3` Python module the MCP server uses."

### 2. `python3-dbus` installed
- Run: `python3 -c 'import dbus'`
- PASS if it exits 0.
- FAIL → "Install the `python3-dbus` system package (Debian/Ubuntu: `sudo apt install python3-dbus`). It is not pulled in automatically by `openvpn3-client`; minimal servers/containers need it explicitly."

### 3. Host DNS / netcfg initialized (once per machine)
- Run: `test -f /var/lib/openvpn3/netcfg.json`
- PASS if the file exists.
- FAIL → "Run these once per machine so the tunnel's pushed DNS reaches systemd-resolved (without this, TCP to private IPs works but `*.rds.amazonaws.com` / `*.cache.amazonaws.com` resolution fails silently with NXDOMAIN):
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```
  Verify with `sudo openvpn3-admin netcfg-service --config-show` — output should include `Systemd-resolved in use: Yes`. Skip on non-systemd hosts (Alpine, minimal Debian without systemd) — hostname access over the tunnel there is out of scope for this plugin."

### 4. Settings file present
- Run: `test -f .claude/openvpn3-on-demand.local.md`
- PASS if it exists.
- FAIL → "Not configured for this project. Run `/openvpn3-on-demand:setup`."

### 5. Settings file valid (only if check 4 passed)
- Parse the YAML frontmatter. Look at `profile_name` and `ovpn_provision_cmd`.
- PASS if exactly one of them is present and non-empty.
- FAIL (both set) → "`profile_name` and `ovpn_provision_cmd` are mutually exclusive — `profile_name` for an existing imported config, `ovpn_provision_cmd` for an ephemeral one. Pick one. Re-run `/openvpn3-on-demand:setup` to fix."
- FAIL (neither set) → "The settings file must declare either `profile_name` (an already-imported config) or `ovpn_provision_cmd` (an ephemeral one). Re-run `/openvpn3-on-demand:setup` to fix."
- Note the mode for the summary: `profile_name` → BYO; `ovpn_provision_cmd` → ephemeral.

### 6. BYO profile imported (only if check 5 passed AND mode is BYO)
- Run: `openvpn3 configs-list` and look for the configured `profile_name`.
- PASS if a config with that name is listed.
- FAIL → "The openvpn3 config `<profile_name>` isn't imported. Import it once (the plugin never creates or removes a BYO config — it only starts/stops sessions for it):
  ```bash
  openvpn3 config-import --config /path/to/your.ovpn --name <profile_name> --persistent
  ```
  The profile must be non-interactive — `auth-user-pass` inlined, no encrypted PKCS#12 — because the MCP server can't answer credential prompts."

### 7. `.gitignore` covers the settings file
- Check the project's `.gitignore` for a line matching `.claude/*.local.md` (or an exact entry for `.claude/openvpn3-on-demand.local.md`).
- PASS if covered.
- FAIL → "Add `.claude/*.local.md` to `.gitignore` — the settings file can contain internal hostnames and provision commands; keep it out of git."
