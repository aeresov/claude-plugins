#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Bootstraps the .venv on first run, then starts the server.
#
# Where /usr/bin/python3 is new enough for the server (>= 3.10), the venv is pinned to it with
# --system-site-packages so the server can `import dbus` / `import openvpn3` from python3-dbus and
# openvpn3-client: that is the openvpn3 backend. Where it is older (Amazon Linux 2023 ships 3.9), uv
# supplies a Python of its own instead. That venv can't see system packages and doesn't need to: such
# hosts have no openvpn3-linux, and the OpenVPN 2 backend is standard library only. The version is
# pinned because uv's default is the newest release, which the lock may not have wheels for yet.
# The server exits 1 with an actionable stderr message if neither backend is usable.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

managed_python=3.12
# The interpreter whose site-packages hold python3-dbus and openvpn3. Overridable for hosts that keep it elsewhere
# (and for the launcher's own tests).
system_python="${OPENVPN_OD_SYSTEM_PYTHON:-/usr/bin/python3}"

if "$system_python" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  # uv records the system interpreter's full version; after a distribution update of python3 the venv no
  # longer matches it, and a bare `uv sync` would swap in one that can't see system site-packages.
  system_version=$("$system_python" -c 'import platform; print(platform.python_version())')
  system_version=${system_version%+} # uv drops the "+" of an unreleased build ("3.12.0+") when it records the version
  venv_fits() {
    grep -qx 'include-system-site-packages = true' .venv/pyvenv.cfg 2>/dev/null || return 1
    # A venv that records no version (not built by uv) is left to `uv sync`, as before.
    case "$(sed -n 's/^version_info = //p' .venv/pyvenv.cfg | head -n 1)" in
      "" | "$system_version" | "$system_version".*) return 0 ;;
      *) return 1 ;;
    esac
  }
  make_venv() { uv venv --python "$system_python" --system-site-packages --quiet; }
else
  venv_fits() { grep -qE "^version_info = ${managed_python//./\\.}(\\.|\$)" .venv/pyvenv.cfg 2>/dev/null; }
  make_venv() { uv venv --python "$managed_python" --quiet; }
fi

# A venv that doesn't fit this host (an older launcher's, another interpreter's) would make `uv sync`
# rebuild it on whatever interpreter uv prefers, so it is replaced here instead.
if ! venv_fits; then
  rm -rf .venv
  make_venv
fi
uv sync --quiet

exec uv run --quiet openvpn-mcp "$@"
