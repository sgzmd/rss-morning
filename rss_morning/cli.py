"""Command-line interface for the rss_morning application."""

from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from .runner import RunConfig, execute

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Fetch recent articles from configured RSS feeds."
    )
    parser.add_argument("-n", "--limit", type=int, default=10, help="Number of articles to fetch.")
    parser.add_argument(
        "--feeds-file", default="feeds.xml", help="Path to the OPML file that defines the feeds."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (e.g. DEBUG, INFO, WARNING).",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help="Only include articles published within the last N hours.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="When set, generate an executive summary using the Gemini API instead of raw article data.",
    )
    parser.add_argument(
        "--email-to",
        help="If provided, send the results to this email address via Resend.",
    )
    parser.add_argument(
        "--email-from",
        help="Sender email address for Resend (defaults to RESEND_FROM_EMAIL env).",
    )
    parser.add_argument(
        "--email-subject",
        help="Subject line to use when emailing results.",
    )
    return parser


def configure_logging(level_name: str) -> None:
    """Initialise logging according to CLI options."""
    log_level = getattr(logging, level_name.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f"Unsupported log level: {level_name}")

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.debug("Logger initialised with level %s", level_name.upper())


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        configure_logging(args.log_level)
    except ValueError as exc:
        parser.error(str(exc))

    config = RunConfig(
        feeds_file=args.feeds_file,
        limit=args.limit,
        max_age_hours=args.max_age_hours,
        summary=args.summary,
        email_to=args.email_to,
        email_from=args.email_from,
        email_subject=args.email_subject,
    )

    try:
        result = execute(config)
    except ValueError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error during execution.")
        return 1

    print(result.output_text)
    return 0
