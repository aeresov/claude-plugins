---
name: openvpn-on-demand-doctor
description: "Diagnose the openvpn-on-demand setup — VPN client (openvpn3 or OpenVPN 2), host readiness, how programs resolve private names, settings file, BYO profile, .gitignore — and report what (if anything) is missing. Read-only; changes nothing."
---

Resolve `../..` from this SKILL.md's directory to the absolute plugin root. Substitute that path for `<plugin-root>` in the instructions and commands below; do not assume a plugin-root shell variable is set. Keep the working directory at the consuming project root.

In Codex, select `$openvpn-on-demand:openvpn-on-demand-setup` or `$openvpn-on-demand:openvpn-on-demand-doctor`. In Claude Code, the equivalent commands are `/openvpn-on-demand:setup` and `/openvpn-on-demand:doctor`.

You are running `openvpn-on-demand-doctor`: a **read-only** health check. Write no files, run nothing privileged, call no `vpn_*` tool.

Optional user-supplied argument: a private hostname or IP for check 9's comparison. Use it only if it starts with a letter or digit and contains only letters, digits, `.`, `-`, `_` (`:` for IPv6); otherwise ignore it and say so.

## Steps

1. Read `<plugin-root>/setup-checklist.md` — checks 1–9 with exact commands and remediation text. Check 1 decides the backend (openvpn3 or openvpn2); checks 2, 4 and 7 have one variant each. For the openvpn2 variants run exactly `python3 "<plugin-root>/servers/openvpn3/src/openvpn3_mcp/ovpn2_helper.py" probe` once and read its JSON.

2. Run checks **1–9 in order** against the current project root:
   - 1 VPN client installed (openvpn3, else OpenVPN 2)
   - 2 backend can be driven (openvpn3: `python3-dbus`; openvpn2: sudo for the helper)
   - 3 `uv` installed
   - 4 host ready for tunnels (openvpn3: netcfg initialized; openvpn2: systemd)
   - 5 settings file present
   - 6 settings file valid (skip if 5 failed)
   - 7 BYO profile imported (only if 6 passed and the mode is BYO)
   - 8 `.gitignore` covers the settings file
   - 9 programs resolve private names (openvpn3: through systemd-resolved; openvpn2: through the host's own resolver) — run exactly `python3 "<plugin-root>/servers/openvpn3/src/openvpn3_mcp/dnscheck.py"`, plus the hostname single-quoted if given. The comparison only means something with a tunnel up (see the script's `link domains` line).

   Read the settings frontmatter for checks 6 and 7.

3. Print one line per check:
   ```
   PASS  1. VPN client installed (openvpn3)
   PASS  2. backend can be driven
   PASS  3. uv installed
   FAIL  4. host ready for tunnels
         → <remediation text from the checklist, verbatim>
   ...
   WARN  9. programs resolve private names
         → <the script's output, verbatim, indented>
   ```
   Name the backend on line 1. Mark checks that don't apply `SKIP`, with a brief reason (e.g. check 7 in ephemeral mode or on openvpn2, checks 6–7 when the settings file is absent; checks 2 and 4 when check 1 found no backend). Only check 9 can `WARN`.

4. End with a one-line summary:
   - All PASS/SKIP → `OK — configured · <openvpn3|openvpn2> · <BYO|ephemeral> mode · <profile name + "imported" (openvpn2: "not checked") | provision cmd set> · host ready`. If `config_overrides` is set, append `· overrides: <k1>=<v1>, <k2>=<v2>` (on openvpn2 add `(not applied on this backend)`).
   - Only check 9 warned → the same line, with `host DNS: see WARN 9` in place of `host ready`.
   - Settings file absent → `Not configured for this project — run openvpn-on-demand-setup` (still report check 9 if it didn't pass).
   - Otherwise → `<n> issue(s) — see the FAIL lines above` (if check 5 or 6 failed, append `; openvpn-on-demand-setup can write/fix the settings file`).

Keep it terse. Don't offer fixes; mention `openvpn-on-demand-setup` only where the checklist does. Never run the `sudo` lines the checklist or script print — they're for the user.
