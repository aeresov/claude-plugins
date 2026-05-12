---
description: Diagnose the openvpn3-on-demand setup — host packages, netcfg init, settings file, BYO profile, .gitignore — and report what (if anything) is missing. Read-only; changes nothing.
allowed-tools: Bash(openvpn3 version), Bash(python3 -c *), Bash(test -f *), Bash(openvpn3 configs-list), Read, Glob
---

You are running the `openvpn3-on-demand` doctor: a **read-only** health check. Do not write
any file, do not run anything privileged, do not call any `vpn_*` MCP tool. Just inspect and report.

## Steps

1. Read the shared checklist at `${CLAUDE_PLUGIN_ROOT}/setup-checklist.md`. It defines
   checks 1–7, exactly how to run each, and the remediation text to print on a failure.

2. Run checks **1 through 7 in order**, against the current project root, exactly as the
   checklist describes:
   - 1 `openvpn3-client` installed · 2 `python3-dbus` installed · 3 host netcfg initialized
   - 4 settings file present · 5 settings file valid (skip if 4 failed) · 6 BYO profile imported
     (only if 5 passed and the mode is BYO) · 7 `.gitignore` covers the settings file
   - To read the settings file's frontmatter for checks 5/6, use the Read tool.

3. Print a result block — one line per check:
   ```
   PASS  1. openvpn3-client installed
   PASS  2. python3-dbus installed
   FAIL  3. host netcfg initialized
         → <the remediation text from the checklist, verbatim>
   ...
   ```
   Use `SKIP` for checks that don't apply (e.g. check 6 in ephemeral mode, check 5/6 when the
   settings file is absent) and say why in one short clause.

4. End with a one-line summary:
   - all green → `OK — configured · <BYO|ephemeral> mode · <profile name + "imported" | provision cmd set> · host ready`
   - settings file absent → `Not configured for this project — run /openvpn3-on-demand:setup`
   - otherwise → `<n> issue(s) — see the FAIL lines above` (and, if check 4/5 is among them, add `; /openvpn3-on-demand:setup can write/fix the settings file`)

Keep it terse. Don't offer to fix anything here — that's what `/openvpn3-on-demand:setup` is for;
just mention it where the checklist's remediation text already does.
