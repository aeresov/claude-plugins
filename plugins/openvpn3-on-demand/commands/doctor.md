---
description: Diagnose the openvpn3-on-demand setup — host packages, netcfg init, whether programs resolve through systemd-resolved, settings file, BYO profile, .gitignore — and report what (if anything) is missing. Read-only; changes nothing.
argument-hint: "[private-hostname]"
allowed-tools: Bash(openvpn3 version), Bash(uv --version), Bash(python3 -c 'import dbus'), Bash(test -f *), Bash(openvpn3 configs-list), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py"), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" *), Read, Glob
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/openvpn3-on-demand-doctor/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.

Pass the user-supplied `$ARGUMENTS` as the optional hostname argument.
