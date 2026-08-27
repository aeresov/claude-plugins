# SPDX-License-Identifier: AGPL-3.0-only
"""argparse front-end for `gl`. Filled in by later tasks."""
from __future__ import annotations

import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    print("usage: gl {api,project,log,diff,artifacts,version} ...", file=sys.stderr)
    return 2
