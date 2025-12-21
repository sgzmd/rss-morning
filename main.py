"""Thin shim for IDEs and direct execution."""

from rss_morning.cli import main

if __name__ == "__main__":
    import sys

    # If no arguments are provided (or just the script name), inject defaults.
    # We want to default to debug logging to a file in logs/
    # Users can override this by providing their own arguments.
    # We check if specific logging args are missing and append them.

    # Simple check: if user didn't provide args, we'll assume they want the default behavior
    # which we are defining as "run with limited set of articles" usually, but here
    # primarily we just want to ensure logging is set up if not specified.
    # However, cli.py parses args. We can just modify sys.argv.

    defaults = []
    if not any(arg.startswith("--log-level") for arg in sys.argv):
        defaults.extend(["--log-level", "DEBUG"])

    if defaults:
        sys.argv.extend(defaults)

    sys.exit(main())
