"""Command-line interface for the rss_morning application."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pprint
from pathlib import Path
from typing import List, Optional

from .config import load_dotenv, parse_app_config
from .runner import RunConfig, execute

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Fetch recent articles from configured RSS feeds."
    )
    parser.add_argument(
        "--config",
        default="configs/config.toml",
        help="Path to the main configuration TOML file.",
    )
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
        "--llm-dry-run",
        action="store_true",
        help="Prepare and log the LLM request without sending it to the API.",
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

    if log_file and str(log_file).strip().lower() not in ("none", "", "null"):
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

        # Load environment variables if an env file is configured or .env exists
        if app_config.env_file:
            load_dotenv(app_config.env_file)
        elif Path(".env").exists():
            load_dotenv(".env")

        # Determine logging settings (CLI overrides Config)
        log_level = args.log_level or app_config.logging.level
        log_file = args.log_file or app_config.logging.file

        configure_logging(log_level, log_file)

        config = RunConfig(
            feeds_file=app_config.feeds_file,
            limit=app_config.limit,
            max_age_hours=app_config.max_age_hours,
            summary=app_config.summary,
            classify=app_config.classification.enabled,
            classify_model=app_config.classification.model,
            classify_threshold=app_config.classification.threshold,
            email_to=app_config.email.to_addr,
            email_from=app_config.email.from_addr,
            email_subject=app_config.email.subject,
            save_articles_path=args.save_articles,
            load_articles_path=args.load_articles,
            max_article_length=app_config.max_article_length,
            system_prompt=app_config.prompt,
            concurrency=app_config.concurrency,
            llm_dry_run=args.llm_dry_run,
            llm_model=app_config.llm_model,
            reasoning_effort=app_config.reasoning_effort,
            digest_config=app_config.digest,
            areas=app_config.areas,
        )

        config_dict = dataclasses.asdict(config)
        if config_dict.get("system_prompt"):
            config_dict["system_prompt"] = "***MASKED***"

        logger.info("Active Configuration:\n%s", pprint.pformat(config_dict))

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
