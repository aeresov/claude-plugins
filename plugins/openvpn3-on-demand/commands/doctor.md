---
description: Diagnose the openvpn3-on-demand setup — host packages, netcfg init, settings file, BYO profile, .gitignore — and report what (if anything) is missing. Read-only; changes nothing.
allowed-tools: Bash(openvpn3 version), Bash(python3 -c *), Bash(test -f *), Bash(openvpn3 configs-list), Read, Glob
---

You are running `/openvpn3-on-demand:doctor`: a **read-only** health check. Write no files, run nothing privileged, call no `vpn_*` tool.

## Steps

1. Read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md` (defines checks 1–7 with exact commands and remediation text).

2. Run checks **1–7 in order** against the current project root:
   - 1 `openvpn3-client` installed
   - 2 `python3-dbus` installed
   - 3 host netcfg initialized
   - 4 settings file present
   - 5 settings file valid (skip if 4 failed)
   - 6 BYO profile imported (only if 5 passed and the mode is BYO)
   - 7 `.gitignore` covers the settings file

   Use the Read tool to parse the settings file's YAML frontmatter for checks 5 and 6.

3. Print one line per check:
   ```
   PASS  1. openvpn3-client installed
   PASS  2. python3-dbus installed
   FAIL  3. host netcfg initialized
         → <remediation text from the checklist, verbatim>
   ...
   ```
   Use `SKIP` for checks that don't apply (e.g. check 6 in ephemeral mode, checks 5–6 when the settings file is absent), with a brief reason.

4. End with a one-line summary:
   - All green → `OK — configured · <BYO|ephemeral> mode · <profile name + "imported" | provision cmd set> · host ready`. If `config_overrides` is set, append `· overrides: <k1>=<v1>, <k2>=<v2>`.
   - Settings file absent → `Not configured for this project — run /openvpn3-on-demand:setup`.
   - Otherwise → `<n> issue(s) — see the FAIL lines above` (if check 4 or 5 failed, append `; /openvpn3-on-demand:setup can write/fix the settings file`).

Keep it terse. Don't offer to fix anything here — mention `/openvpn3-on-demand:setup` only where the checklist's remediation text already does.
