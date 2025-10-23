"""High-level orchestration for the rss_morning application."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from .articles import fetch_article_text, truncate_text
from .config import parse_feeds_config
from .emailing import send_email_report
from .feeds import fetch_feed_entries, select_recent_entries
from .summaries import generate_summary

logger = logging.getLogger(__name__)


@dataclass
class RunConfig:
    """Runtime options for executing the application."""

    feeds_file: str
    limit: int
    max_age_hours: Optional[float]
    summary: bool
    email_to: Optional[str]
    email_from: Optional[str]
    email_subject: Optional[str]


@dataclass
class RunResult:
    """Returned data after executing the app."""

    output_text: str
    email_payload: Any
    is_summary: bool


def _collect_entries(config: RunConfig) -> List[dict]:
    feeds = parse_feeds_config(config.feeds_file)
    if not feeds:
        raise RuntimeError("No feeds found in the configuration.")

    cutoff: Optional[datetime] = None
    if config.max_age_hours is not None:
        if config.max_age_hours <= 0:
            raise ValueError("--max-age-hours must be positive.")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=config.max_age_hours)
        logger.info("Applying article cutoff: newer than %s", cutoff)

    feed_entries = []
    for feed in feeds:
        try:
            entries = fetch_feed_entries(feed)
            feed_entries.extend(entries)
        except Exception:
            logger.exception("Failed to process feed %s", feed.url)

    if not feed_entries:
        raise RuntimeError("No entries were retrieved from the configured feeds.")

    selected_entries = select_recent_entries(feed_entries, config.limit, cutoff)
    logger.info("Fetching article text for %d selected entries", len(selected_entries))

    output = []
    for entry in selected_entries:
        text = fetch_article_text(entry.link)
        payload = {
            "url": entry.link,
            "category": entry.category,
            "title": entry.title,
            "summary": entry.summary or "",
        }
        if text:
            payload["text"] = truncate_text(text)
        else:
            logger.info("Article text unavailable; including metadata only: %s", entry.link)

        output.append(payload)

    logger.info("Completed processing. Outputting %d articles as JSON.", len(output))
    return output


def execute(config: RunConfig) -> RunResult:
    """Run the application logic and return the result payload."""
    articles = _collect_entries(config)
    email_payload: Any = articles
    is_summary_payload = False

    if config.summary:
        summary_output, summary_data = generate_summary(articles, return_dict=True)
        output_text = summary_output
        if summary_data is not None:
            email_payload = summary_data
            is_summary_payload = True
    else:
        output_text = json.dumps(articles, indent=2, ensure_ascii=False)

    if config.email_to:
        send_email_report(
            payload=email_payload,
            is_summary=is_summary_payload,
            to_address=config.email_to,
            from_address=config.email_from,
            subject=config.email_subject,
        )

    return RunResult(output_text=output_text, email_payload=email_payload, is_summary=is_summary_payload)
