"""Command-line interface for the rss_morning application."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List, Optional

from .runner import RunConfig, execute

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Fetch recent articles from configured RSS feeds."
    )
    parser.add_argument(
        "-n", "--limit", type=int, default=10, help="Number of articles to fetch."
    )
    parser.add_argument(
        "--feeds-file",
        default="feeds.xml",
        help="Path to the OPML file that defines the feeds.",
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
        "--pre-filter",
        nargs="?",
        const=True,
        default=None,
        metavar="EMBED_PATH",
        help="Apply an embedding-based pre-filter to articles after download. Optionally provide a path to precomputed query embeddings.",
    )
    parser.add_argument(
        "--cluster-threshold",
        type=float,
        default=0.8,
        help="Cosine similarity threshold for clustering near-duplicate articles during pre-filtering.",
    )
    parser.add_argument(
        "--save-articles",
        metavar="PATH",
        help="Write the fetched articles to PATH as JSON before further processing.",
    )
    parser.add_argument(
        "--load-articles",
        metavar="PATH",
        help="Load pre-fetched articles from PATH instead of fetching feeds.",
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
    parser.add_argument(
        "--log-file",
        help="Optional path to a log file; when provided logs are written to both console and the file.",
    )
    return parser


def configure_logging(level_name: str, log_file: Optional[str] = None) -> None:
    """Initialise logging according to CLI options."""
    log_level = getattr(logging, level_name.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f"Unsupported log level: {level_name}")

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(log_level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    if log_file:
        log_path = Path(log_file)
        if log_path.parent and not log_path.parent.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        logger.debug(
            "Logger initialised with level %s and file output to %s",
            level_name.upper(),
            log_path,
        )
    else:
        logger.debug(
            "Logger initialised with console output at level %s", level_name.upper()
        )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        configure_logging(args.log_level, args.log_file)
    except ValueError as exc:
        parser.error(str(exc))

    config = RunConfig(
        feeds_file=args.feeds_file,
        limit=args.limit,
        max_age_hours=args.max_age_hours,
        summary=args.summary,
        pre_filter=bool(args.pre_filter),
        pre_filter_embeddings_path=(
            args.pre_filter if isinstance(args.pre_filter, str) else None
        ),
        email_to=args.email_to,
        email_from=args.email_from,
        email_subject=args.email_subject,
        cluster_threshold=args.cluster_threshold,
        save_articles_path=args.save_articles,
        load_articles_path=args.load_articles,
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
