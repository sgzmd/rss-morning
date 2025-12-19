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

    # Check if we should log to stdout (cloud-friendly) or default to file
    import os

    if os.environ.get("RSS_MORNING_LOG_STDOUT") == "1":
        # Do not add --log-file; application defaults to stderr/stdout usually if no file specified?
        # Attempting to check cli.py to see default behavior if not file is provided.
        # Assuming cli.py handles it, or we pass nothing to let it default (often stdout).
        pass
    elif not any(arg.startswith("--log-file") for arg in sys.argv):
        defaults.extend(["--log-file", "logs/rss-morning.log"])

    if defaults:
        sys.argv.extend(defaults)

    sys.exit(main())
