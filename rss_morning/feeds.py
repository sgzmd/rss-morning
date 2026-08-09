"""Feed parsing and selection helpers."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import feedparser
from bs4 import BeautifulSoup
import re

from .http_client import DownloadError, HttpClient
from .models import FeedConfig, FeedEntry

logger = logging.getLogger(__name__)
_DEFAULT_HTTP_CLIENT = HttpClient(max_bytes=5_242_880)


def to_datetime(value: Optional[time.struct_time]) -> datetime:
    """Convert feedparser timestamps to timezone-aware datetimes."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)


def fetch_feed_entries(
    feed: FeedConfig, http_client: Optional[HttpClient] = None
) -> List[FeedEntry]:
    """Fetch entries from a single RSS feed definition."""
    logger.info("Fetching feed '%s' (%s)", feed.title, feed.url)
    try:
        response = (http_client or _DEFAULT_HTTP_CLIENT).get(
            feed.url,
            accepted_content_types=(
                "application/rss+xml",
                "application/atom+xml",
                "application/xml",
                "text/xml",
            ),
            allow_missing_content_type=True,
        )
        response_content = response.body
    except DownloadError as e:
        logger.warning("Failed to fetch feed '%s' (%s): %s", feed.title, feed.url, e)
        return []

    parsed = feedparser.parse(response_content)
    entries: List[FeedEntry] = []

    for entry in parsed.entries:
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", None)

        if not link or not title:
            logger.debug("Skipping entry without link or title in feed '%s'", feed.url)
            continue

        summary = getattr(entry, "summary", None)
        if not summary:
            summary_detail = getattr(entry, "summary_detail", None)
            if summary_detail:
                summary = summary_detail.get("value")
        if not summary:
            entry_content = getattr(entry, "content", None)
            if entry_content:
                try:
                    summary = entry_content[0].get("value")
                except (TypeError, KeyError, IndexError, AttributeError):
                    summary = None
        if summary:
            summary = _strip_html(summary)

        published = None
        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            published = getattr(entry, attr, None)
            if published:
                break

        entries.append(
            FeedEntry(
                link=link,
                title=title,
                category=feed.category,
                published=to_datetime(published),
                summary=summary,
            )
        )

    logger.info("Collected %d entries from feed '%s'", len(entries), feed.url)
    return entries


def _strip_html(raw_value: str) -> str:
    """Return text content extracted from HTML fragments."""
    soup = BeautifulSoup(raw_value, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def select_recent_entries(
    entries: Iterable[FeedEntry],
    limit: int,
    cutoff: Optional[datetime] = None,
) -> List[FeedEntry]:
    """Return the newest unique entries respecting limit and optional cutoff."""
    sorted_entries = sorted(entries, key=lambda item: item.published, reverse=True)
    seen_links = set()
    unique_entries: List[FeedEntry] = []

    for entry in sorted_entries:
        if cutoff and entry.published < cutoff:
            logger.debug(
                "Skipping entry older than cutoff (%s < %s): %s",
                entry.published,
                cutoff,
                entry.link,
            )
            continue
        if entry.link in seen_links:
            continue
        unique_entries.append(entry)
        seen_links.add(entry.link)
        if len(unique_entries) >= limit:
            break

    logger.info(
        "Selected %d unique recent entries (requested %d)", len(unique_entries), limit
    )
    return unique_entries
