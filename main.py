"""Thin shim for IDEs and direct execution."""

import requests.adapters  # noqa: F401

from rss_morning.cli import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
