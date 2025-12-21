"""Command-line interface for the rss_morning application."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import List, Optional

from .config import parse_app_config
from .runner import RunConfig, execute

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Fetch recent articles from configured RSS feeds."
    )
    # New main config argument
    parser.add_argument(
        "--config",
        default="configs/config.xml",
        help="Path to the main configuration XML file.",
    )

    # Overrides for logging/debugging
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (e.g. DEBUG, INFO, WARNING). Overrides config.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional path to a log file. Overrides config.",
    )

    # Runtime execution flags that might not be in config (debugging mostly)
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

    return parser


def configure_logging(level_name: str, log_file: Optional[str] = None) -> None:
    """Initialise logging according to options."""
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
        # Load main config
        app_config = parse_app_config(args.config)

        # Load env config if present - NOT for secrets anymore, but handled by parse_app_config
        # We can still load it if there are other env vars, but load_secrets handles the keys.
        # If we remove os.environ.update, implicit usage (if any remains) would break.
        # But we refactored components to use explicit keys.
        # So we can remove this block or comment it out if env.xml is strictly for secrets.
        # However, parse_app_config calls load_secrets which reads env.xml variables.
        # So we don't need to push them to os.environ globally.

        # Determine logging settings (CLI overrides Config)
        log_level = args.log_level or app_config.logging.level
        log_file = args.log_file

        # AWS Deployment Guide 1.3: Cloud-Friendly Logging
        if os.environ.get("RSS_MORNING_LOG_STDOUT") == "1":
            log_file = None

        configure_logging(log_level, log_file)

        # Create RunConfig
        config = RunConfig(
            feeds_file=app_config.feeds_file,
            limit=app_config.limit,
            max_age_hours=app_config.max_age_hours,
            summary=app_config.summary,
            pre_filter=app_config.pre_filter.enabled,
            pre_filter_embeddings_path=app_config.pre_filter.embeddings_path,
            email_to=app_config.email.to_addr,
            email_from=None,  # Handled via SecretsConfig
            email_subject=app_config.email.subject,
            cluster_threshold=app_config.pre_filter.cluster_threshold,
            save_articles_path=args.save_articles,
            load_articles_path=args.load_articles,
            max_article_length=app_config.max_article_length,
            system_prompt=app_config.prompt,
            secrets=app_config.secrets,
        )

        result = execute(config)
    except ValueError as exc:
        parser.error(str(exc))
    except (RuntimeError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error during execution.")
        return 1

    print(result.output_text)
    return 0
