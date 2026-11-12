# Setup checklist (shared reference)

Read by `openvpn-on-demand-setup` and `openvpn-on-demand-doctor`: the single source for the 9 checks and their remediation text. `doctor` reports them; `setup` reports, then fixes the ones it owns. Kept at the plugin root so it isn't a slash command.

All checks are read-only, need no `sudo`, and run from the project root.

## Two backends

The MCP server drives **openvpn3-linux** where it is installed, and the distribution's **OpenVPN 2.6** on hosts that have no openvpn3 package (Amazon Linux 2023, RHEL-likes without the upstream repo). Check 1 finds out which this host has; checks 2, 4 and 7 then use the variant for that backend. The OpenVPN 2 variants read one command's output:

```bash
python3 "<plugin root>/servers/openvpn3/src/openvpn3_mcp/ovpn2_helper.py" probe
```

It prints one JSON line, e.g. `{"status": "ok", "protocol": 1, "openvpn": "OpenVPN 2.6.12 …", "supported": true, "systemd_run": true, "sudo": true, "helper": "bundled"}` (`helper`: `bundled`, or `installed` / `stale` for a root-owned copy under `/usr/local/libexec`). It runs nothing privileged: `sudo` is only *asked* (`sudo -n -l`) whether it would run the helper.

## Checks

### 1. VPN client installed
- Run: `openvpn3 version` — exit 0 → PASS, backend **openvpn3**.
- Otherwise run the probe above — `"supported": true` → PASS, backend **openvpn2**.
- FAIL (neither) → "No VPN client the plugin can drive. Install one:
  - openvpn3-linux — the `openvpn3-client` package (Debian/Ubuntu: `sudo apt install openvpn3-client`; Fedora/RHEL: enable the upstream OpenVPN 3 repo). It provides the D-Bus services and the `openvpn3` Python module the MCP server uses.
  - or OpenVPN 2.6 — the distribution's `openvpn` package (Amazon Linux 2023: `sudo dnf install openvpn`). The probe's `openvpn` field says what it found; versions other than 2.6.x aren't supported.

  Then restart the client."

### 2. Backend can be driven
- **openvpn3** — Run: `python3 -c 'import dbus'` — PASS on exit 0.
  - FAIL → "Install the `python3-dbus` package (Debian/Ubuntu: `sudo apt install python3-dbus`). `openvpn3-client` doesn't pull it in."
- **openvpn2** — PASS if the probe says `"sudo": true` and `"helper"` is not `"stale"`.
  - SKIP (`"sudo": null`) → "This shell runs under a command sandbox (the kernel's no-new-privileges flag), where sudo cannot work whatever its rules say, so this could not be checked here. The MCP server runs outside the sandbox; run the doctor from an unsandboxed shell to check sudo."
  - FAIL (`"helper": "stale"`) → "The helper installed at `/usr/local/libexec/openvpn-on-demand/ovpn2-helper` is from another plugin version and won't be used to start a tunnel. Re-run the first line below."
  - FAIL → "OpenVPN 2 needs root to start a tunnel, and the plugin gets it through `sudo -n`. Either give this user passwordless sudo, or allow only the plugin's helper (it vets every profile and is the only thing that runs as root):
    ```bash
    sudo install -D -o root -g root -m 0755 "<plugin root>/servers/openvpn3/src/openvpn3_mcp/ovpn2_helper.py" /usr/local/libexec/openvpn-on-demand/ovpn2-helper
    echo "$USER ALL=(root) NOPASSWD: /usr/local/libexec/openvpn-on-demand/ovpn2-helper" | sudo tee /etc/sudoers.d/openvpn-on-demand
    sudo visudo -cf /etc/sudoers.d/openvpn-on-demand
    ```
    Re-run the first line after each plugin update."

### 3. `uv` installed
- Run: `uv --version` — PASS if it prints a version.
- FAIL → "`uv` isn't on `PATH`. It runs the MCP server (`servers/openvpn3/scripts/launch.sh`); without it no `vpn_*` tool exists. Install it (https://docs.astral.sh/uv/), then restart the client."

### 4. Host ready for tunnels (once per machine)
- **openvpn2** — PASS if the probe says `"systemd_run": true` (tunnels run as transient systemd units). There is no DNS step: this backend leaves DNS alone, and names resolve as they do without the tunnel (check 9 looks at that).
  - FAIL → "The OpenVPN 2 backend needs systemd (`systemd-run`); it doesn't work on this host."
- **openvpn3** — Run: `test -f /var/lib/openvpn3/netcfg.json` — PASS if it exists.
- FAIL (openvpn3) → "Run once per machine so the tunnel's DNS reaches systemd-resolved (without it, private IPs work but names like `*.rds.amazonaws.com` fail or come back as `0.0.0.0`):
  ```bash
  sudo openvpn3-admin init-config --write-configs --force
  sudo openvpn3-admin netcfg-service --config-set systemd-resolved true
  sudo killall -INT openvpn3-service-netcfg
  ```
  Verify: `sudo openvpn3-admin netcfg-service --config-show` includes `Systemd-resolved in use: Yes`. Skip on non-systemd hosts (Alpine etc.); DNS over the tunnel there is out of scope."

### 5. Settings file present
- Run: `test -f .claude/openvpn-on-demand.local.md` — PASS if it exists.
- FAIL → "Not configured for this project. Run `openvpn-on-demand-setup`."

### 6. Settings file valid (only if check 5 passed)
- Parse the YAML frontmatter — PASS if exactly one of `profile_name` / `ovpn_provision_cmd` is present and non-empty.
- FAIL (both) → "`profile_name` and `ovpn_provision_cmd` are mutually exclusive — pick one. Re-run `openvpn-on-demand-setup` to fix."
- FAIL (neither) → "The settings file must declare `profile_name` (BYO) or `ovpn_provision_cmd` (ephemeral). Re-run `openvpn-on-demand-setup` to fix."
- Record the mode for the summary: `profile_name` → BYO, `ovpn_provision_cmd` → ephemeral.

### 7. BYO profile imported (only if check 6 passed AND mode is BYO)
- **openvpn3** — Run: `openvpn3 configs-list` — PASS if the configured `profile_name` is listed.
  - FAIL → "The openvpn3 config `<profile_name>` isn't imported. Import it once (the plugin never creates or removes BYO configs):
    ```bash
    openvpn3 config-import --config /path/to/your.ovpn --name <profile_name> --persistent
    ```
    The profile must be non-interactive (`auth-user-pass` inlined, no encrypted PKCS#12) — the MCP server can't answer credential prompts."
- **openvpn2** — SKIP: the profile is `/etc/openvpn/client/<profile_name>.conf`, which only root can read; `vpn_connect` reports a missing one. Tell the user where it has to be:
  ```bash
  sudo install -m 600 -o root -g root /path/to/your.ovpn /etc/openvpn/client/<profile_name>.conf
  ```
  The name may hold letters, digits, `_`, `.` and `-`. The profile must be non-interactive and pass the backend's allowlist (README, "OpenVPN 2 backend"): no scripts, plugins, log files or routes of its own.

### 8. `.gitignore` covers the settings file
- Run: `git check-ignore -q .claude/openvpn-on-demand.local.md` — PASS on exit 0: any pattern that covers the file counts (a project that ignores all of `.claude/` passes too). Outside a git repository (exit 128), PASS if `.gitignore` has `.claude/*.local.md` or `.claude/openvpn-on-demand.local.md`.
- FAIL (exit 1, and `.gitignore` has neither of those two lines) → "Add `.claude/*.local.md` to `.gitignore` — the settings file can hold internal hostnames and provisioning commands."
- FAIL (exit 1 although `.gitignore` has one of those lines) → "`.gitignore` lists the settings file, but git already tracks it (or a later `!` line re-includes it), so it will be committed. Stop tracking it: `git rm --cached .claude/openvpn-on-demand.local.md` (the file stays on disk), then commit."

### 9. Programs resolve private names (once per machine)
- Run: `python3 "<plugin root>/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" [<hostname>]` (`doctor` and `setup` give the full path). Pass a private hostname when one is known; with the tunnel up it adds a comparison of both lookup paths. Without one it only reads the resolver setup.
- PASS on exit 0 (including its `SKIP:` on hosts without systemd-resolved), WARN on 3, FAIL on 1. Exit 2 = hostname rejected: report it, rerun without one.
- On a host without openvpn3 the script says the VPN leaves DNS alone and judges a hostname only by what programs get for it (a private address passes). It prints no fix there; nothing about that host's resolver needs changing.
- WARN / FAIL → print the output as-is: what owns `/etc/resolv.conf`, what programs got, and the root-only fix for the user. Check 4 gets the tunnel's DNS *into* systemd-resolved; this checks that programs *ask* it. If Tailscale, a VPN client or NetworkManager owns `/etc/resolv.conf` and nsswitch has no `resolve`, private names fail or return `0.0.0.0` (clients then hit this machine or hang) while check 4 and `resolvectl query` look fine.
