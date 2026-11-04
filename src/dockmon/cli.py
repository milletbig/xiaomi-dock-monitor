"""Command-line entry point.

``python -m dockmon``          launch the UI
``python -m dockmon --dump``   print a JSON snapshot and exit
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dockmon",
        description="Xiaomi 10-in-1 USB-C dock real-time monitor for Windows",
    )
    parser.add_argument("--dump", action="store_true", help="print a JSON snapshot and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("This tool currently supports Windows only.", file=sys.stderr)
        return 2

    if args.dump:
        from .dock import build_snapshot

        snapshot = build_snapshot()
        print(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2))
        return 0

    from .ui import run_ui

    return run_ui()


if __name__ == "__main__":
    raise SystemExit(main())
