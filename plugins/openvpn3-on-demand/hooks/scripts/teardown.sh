#!/bin/bash
# Safety-net teardown for openvpn3-on-demand.
#
# Fires on Stop and SessionEnd. Reads the configured profile_name from the
# project's .claude/openvpn3-on-demand.local.md and disconnects that one
# profile iff it's currently active. Idempotent no-op otherwise.
#
# Scoping rule: only touches sessions whose config name matches the profile
# declared in the per-project settings file. Never blanket-disconnects.

set -euo pipefail

STATE_FILE=".claude/openvpn3-on-demand.local.md"

# Not configured for this project -> nothing to do.
[[ -f "$STATE_FILE" ]] || exit 0

# openvpn3 not installed -> nothing we can do; don't fail the hook.
command -v openvpn3 >/dev/null 2>&1 || exit 0

# Extract profile_name from YAML frontmatter between the --- markers.
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$STATE_FILE")
PROFILE=$(echo "$FRONTMATTER" \
  | grep '^profile_name:' \
  | head -n1 \
  | sed 's/profile_name:[[:space:]]*//' \
  | sed 's/^"\(.*\)"$/\1/' \
  | sed "s/^'\(.*\)'\$/\1/")

[[ -n "$PROFILE" ]] || exit 0

# Is that profile currently connected?
SESSIONS=$(openvpn3 sessions-list 2>/dev/null || true)
if echo "$SESSIONS" | grep -qE "^(Config name|Config):[[:space:]]*${PROFILE}[[:space:]]*\$"; then
  openvpn3 session-manage --config "$PROFILE" --disconnect >/dev/null 2>&1 || true
fi

exit 0
