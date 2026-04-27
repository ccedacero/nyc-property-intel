"""Entry point for the nyc-property-intel-sync console script.

Delegates to scripts/sync_all.py. Railway sets CWD to /app (project root)
so scripts/ is always resolvable via os.getcwd().
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    scripts_dir = os.path.join(os.getcwd(), "scripts")
    sys.path.insert(0, scripts_dir)
    from sync_all import main as _main  # type: ignore[import]
    _main()
