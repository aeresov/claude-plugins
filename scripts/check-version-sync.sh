#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Assert each plugin's version is in sync: .claude-plugin/plugin.json ==
# its .claude-plugin/marketplace.json entry == any in-tree pyproject.toml.
# Run from the repo root; exits non-zero on drift. Called by CI.

set -euo pipefail
shopt -s nullglob

fail=0
err() { echo "::error::$1"; fail=1; }

declare -A mkt
while read -r n v; do mkt[$n]=$v; done < <(jq -r '.plugins[] | "\(.name) \(.version)"' .claude-plugin/marketplace.json)

for p in plugins/*/; do
  read -r name pv < <(jq -r '"\(.name) \(.version)"' "$p.claude-plugin/plugin.json")
  [ "$pv" = "${mkt[$name]-}" ] || err "$name: plugin.json $pv != marketplace.json ${mkt[$name]-<no entry>}"
  while IFS= read -r proj; do
    tv=$(grep -m1 -oP '^version *= *"\K[^"]*' "$proj" || true)
    [ "$tv" = "$pv" ] || err "$name: $proj ${tv:-<no version>} != plugin.json $pv"
  done < <(find "$p" -name pyproject.toml -not -path '*/.venv/*')
done
exit "$fail"
