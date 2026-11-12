---
description: Diagnose the openvpn-on-demand setup — VPN client (openvpn3 or OpenVPN 2), host readiness, how programs resolve private names, settings file, BYO profile, .gitignore — and report what (if anything) is missing. Read-only; changes nothing.
argument-hint: "[private-hostname]"
allowed-tools: Bash(openvpn3 version), Bash(uv --version), Bash(python3 -c 'import dbus'), Bash(test -f *), Bash(git check-ignore -q .claude/openvpn-on-demand.local.md), Bash(openvpn3 configs-list), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py"), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/ovpn2_helper.py" probe), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn3/src/openvpn3_mcp/dnscheck.py" *), Read, Glob
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/openvpn-on-demand-doctor/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.

Pass the user-supplied `$ARGUMENTS` as the optional hostname argument.
