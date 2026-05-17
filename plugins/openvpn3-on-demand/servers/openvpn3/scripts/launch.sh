#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Bootstraps the .venv on first run with --system-site-packages pinned to
# /usr/bin/python3 so the server can `import dbus` / `import openvpn3`
# from python3-dbus and openvpn3-client. The server itself exits 1 with
# an actionable stderr message if those imports fail.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

[ -d .venv ] || uv venv --python /usr/bin/python3 --system-site-packages --quiet
uv sync --quiet

exec uv run --quiet openvpn3-mcp "$@"
