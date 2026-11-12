---
description: Configure openvpn-on-demand for this project — pick BYO vs ephemeral mode, write .claude/openvpn-on-demand.local.md, and add it to .gitignore. Read-only against the host; runs nothing privileged.
allowed-tools: Bash(openvpn3 version), Bash(uv --version), Bash(python3 -c 'import dbus'), Bash(test -f *), Bash(git check-ignore -q .claude/openvpn-on-demand.local.md), Bash(openvpn3 configs-list), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn/src/openvpn_mcp/dnscheck.py"), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/servers/openvpn/src/openvpn_mcp/ovpn2_helper.py" probe), Read, Glob, Write, Edit, AskUserQuestion
---

Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/openvpn-on-demand-setup/SKILL.md`. Resolve `<plugin-root>` there to `${CLAUDE_PLUGIN_ROOT}`.
