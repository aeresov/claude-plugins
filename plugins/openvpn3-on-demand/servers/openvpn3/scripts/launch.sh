#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Launcher for the openvpn3 MCP server.
#
# The server imports `dbus` and `openvpn3` from the system Python site
# directory (Debian/Ubuntu ships them in /usr/lib/python3/dist-packages as
# part of python3-dbus / openvpn3-client). uv creates venvs with
# include-system-site-packages=false by default, so without this wrapper
# the server can't see the two modules it needs.
#
# On every invocation we make sure ./.venv exists AND is configured with
# system-site-packages, recreating it if not. Then `uv sync` layers mcp on
# top, and `uv run` starts the stdio server.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${HERE}/.venv"

if ! command -v uv >/dev/null 2>&1; then
  echo "openvpn3-mcp: 'uv' not found on PATH. Install uv from https://docs.astral.sh/uv/ and restart Claude Code." >&2
  exit 127
fi

# Belt-and-suspenders: refuse to rm -rf anything that isn't a .venv directory.
if [ "${VENV##*/}" != ".venv" ]; then
  echo "openvpn3-mcp: refusing to touch ${VENV} (not named .venv)" >&2
  exit 2
fi

cd "${HERE}"

ensure_venv() {
  if [ ! -f "${VENV}/pyvenv.cfg" ] || ! grep -q "^include-system-site-packages = true" "${VENV}/pyvenv.cfg"; then
    rm -rf "${VENV}"
    uv venv --system-site-packages --quiet
  fi
}

# Run once, sync, then re-run. `uv sync` won't normally rebuild the venv, but
# if it ever does (Python-version mismatch, broken interpreter) the new venv
# would drop include-system-site-packages=true and the next `uv run` would
# crash on `import dbus`. Cheap to recheck.
ensure_venv
uv sync --quiet
ensure_venv

exec uv run --quiet openvpn3-mcp "$@"
