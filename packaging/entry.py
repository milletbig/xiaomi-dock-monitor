"""Entry point for the frozen single-file executable."""

from __future__ import annotations

from dockmon.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
