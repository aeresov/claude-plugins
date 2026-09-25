---
description: Diagnose the openvpn3-on-demand setup — host packages, netcfg init, whether programs resolve through systemd-resolved, settings file, BYO profile, .gitignore — and report what (if anything) is missing. Read-only; changes nothing.
argument-hint: "[private-hostname]"
allowed-tools: Bash(openvpn3 version), Bash(uv --version), Bash(python3 -c 'import dbus'), Bash(test -f *), Bash(openvpn3 configs-list), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py"), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" *), Read, Glob
---

You are running `/openvpn3-on-demand:doctor`: a **read-only** health check. Write no files, run nothing privileged, call no `vpn_*` tool.

Optional `$ARGUMENTS`: a private hostname or IP for check 9's comparison. Use it only if it starts with a letter or digit and contains only letters, digits, `.`, `-`, `_` (`:` for IPv6); otherwise ignore it and say so.

## Steps

1. Read `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` — checks 1–9 with exact commands and remediation text.

2. Run checks **1–9 in order** against the current project root:
   - 1 `openvpn3-client` installed
   - 2 `python3-dbus` installed
   - 3 `uv` installed
   - 4 host netcfg initialized
   - 5 settings file present
   - 6 settings file valid (skip if 5 failed)
   - 7 BYO profile imported (only if 6 passed and the mode is BYO)
   - 8 `.gitignore` covers the settings file
   - 9 programs resolve through systemd-resolved — run exactly `python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py"`, plus the hostname single-quoted if given. The comparison only means something with a tunnel up (see the script's `link domains` line).

   Parse the settings frontmatter with the Read tool for checks 6 and 7.

3. Print one line per check:
   ```
   PASS  1. openvpn3-client installed
   PASS  2. python3-dbus installed
   PASS  3. uv installed
   FAIL  4. host netcfg initialized
         → <remediation text from the checklist, verbatim>
   ...
   WARN  9. programs resolve through systemd-resolved
         → <the script's output, verbatim, indented>
   ```
   Mark checks that don't apply `SKIP`, with a brief reason (e.g. check 7 in ephemeral mode, checks 6–7 when the settings file is absent). Only check 9 can `WARN`.

4. End with a one-line summary:
   - All PASS/SKIP → `OK — configured · <BYO|ephemeral> mode · <profile name + "imported" | provision cmd set> · host ready`. If `config_overrides` is set, append `· overrides: <k1>=<v1>, <k2>=<v2>`.
   - Only check 9 warned → the same line, with `host DNS: see WARN 9` in place of `host ready`.
   - Settings file absent → `Not configured for this project — run /openvpn3-on-demand:setup` (still report check 9 if it didn't pass).
   - Otherwise → `<n> issue(s) — see the FAIL lines above` (if check 5 or 6 failed, append `; /openvpn3-on-demand:setup can write/fix the settings file`).

Keep it terse. Don't offer fixes; mention `/openvpn3-on-demand:setup` only where the checklist does. Never run the `sudo` lines the checklist or script print — they're for the user.
