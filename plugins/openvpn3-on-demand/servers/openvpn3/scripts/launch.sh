#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Launcher for the openvpn3 MCP server.
#
# The server imports `dbus` and `openvpn3` from the *system* Python site
# directory — Debian/Ubuntu ships them in /usr/lib/python3/dist-packages as
# part of python3-dbus / openvpn3-client; Fedora/RHEL puts them under
# /usr/lib/pythonX.Y/site-packages. Two things have to be true for the
# venv to see them:
#
#   1. The venv must enable include-system-site-packages.
#   2. The venv's *parent interpreter* must be the system Python — that's
#      whose site-packages "system-site-packages" actually refers to.
#
# (2) is the part that bit users in 0.4.0–0.4.2: `uv venv` defaults to a
# uv-managed Python (e.g. ~/.local/share/uv/python/cpython-3.13-...), and
# that interpreter's site-packages does NOT include /usr/lib/python3/...,
# so `import dbus` fails with ModuleNotFoundError even with the system
# packages installed. Clients then see the generic "openvpn3 Python module
# or dbus-python is not available" error from every MCP tool call.
#
# Fix: pin the venv's parent interpreter to /usr/bin/python3 explicitly,
# and recreate any pre-existing venv that points elsewhere.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${HERE}/.venv"
SYSTEM_PYTHON="/usr/bin/python3"
SYSTEM_PYTHON_DIR="$(dirname "${SYSTEM_PYTHON}")"

if ! command -v uv >/dev/null 2>&1; then
  echo "openvpn3-mcp: 'uv' not found on PATH. Install uv from https://docs.astral.sh/uv/ and restart Claude Code." >&2
  exit 127
fi

if [ ! -x "${SYSTEM_PYTHON}" ]; then
  echo "openvpn3-mcp: ${SYSTEM_PYTHON} not found. The MCP server needs the system Python interpreter so its venv can see the dbus / openvpn3 modules from openvpn3-client and python3-dbus." >&2
  exit 127
fi

# Belt-and-suspenders: refuse to rm -rf anything that isn't a .venv directory.
if [ "${VENV##*/}" != ".venv" ]; then
  echo "openvpn3-mcp: refusing to touch ${VENV} (not named .venv)" >&2
  exit 2
fi

cd "${HERE}"

ensure_venv() {
  # Recreate the venv if it's missing, doesn't have system-site-packages
  # enabled, OR was created against a non-system interpreter (e.g. an old
  # launcher run that let uv pick a uv-managed Python).
  if [ ! -f "${VENV}/pyvenv.cfg" ] \
     || ! grep -q "^include-system-site-packages = true" "${VENV}/pyvenv.cfg" \
     || ! grep -q "^home = ${SYSTEM_PYTHON_DIR}\$" "${VENV}/pyvenv.cfg"; then
    rm -rf "${VENV}"
    uv venv --python "${SYSTEM_PYTHON}" --system-site-packages --quiet
  fi
}

# Run once, sync, then re-run. `uv sync` won't normally rebuild the venv, but
# if it ever does (Python-version mismatch, broken interpreter) the new venv
# would drop include-system-site-packages=true (or get pinned to a uv-managed
# Python) and the next `uv run` would crash on `import dbus`. Cheap to
# recheck.
ensure_venv
uv sync --quiet
ensure_venv

exec uv run --quiet openvpn3-mcp "$@"
