"""Thin shim for IDEs and direct execution."""

from rss_morning.cli import main

if __name__ == "__main__":
    import sys

    defaults = []
    if not any(arg.startswith("--log-level") for arg in sys.argv):
        defaults.extend(["--log-level", "DEBUG"])

    if defaults:
        sys.argv.extend(defaults)

    sys.exit(main())
