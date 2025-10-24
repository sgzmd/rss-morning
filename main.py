"""Thin shim for IDEs and direct execution."""

from rss_morning.cli import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
