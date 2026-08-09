"""Feed parsing and selection helpers."""

from __future__ import annotations

import logging
import calendar
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Mapping, Optional

import feedparser
from bs4 import BeautifulSoup
import re

from .http_client import DownloadError, HttpClient
from .models import FeedConfig, FeedEntry

logger = logging.getLogger(__name__)
_DEFAULT_HTTP_CLIENT = HttpClient(max_bytes=5_242_880)
_FEED_CONTENT_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
)


def to_datetime(value: Optional[time.struct_time]) -> datetime:
    """Convert feedparser timestamps to timezone-aware datetimes."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)


def fetch_feed_entries(
    feed: FeedConfig,
    http_client: Optional[HttpClient] = None,
    cached_state: Optional[Mapping[str, object]] = None,
    on_cache_update: Optional[Callable[[dict], None]] = None,
) -> List[FeedEntry]:
    """Fetch entries from a single RSS feed definition."""
    logger.info("Fetching feed '%s' (%s)", feed.title, feed.url)
    conditional_headers = {}
    if cached_state:
        etag = cached_state.get("etag")
        last_modified = cached_state.get("last_modified")
        if isinstance(etag, str) and etag:
            conditional_headers["If-None-Match"] = etag
        if isinstance(last_modified, str) and last_modified:
            conditional_headers["If-Modified-Since"] = last_modified

    try:
        client = http_client or _DEFAULT_HTTP_CLIENT
        response = _download_feed(client, feed.url, conditional_headers or None)
        if response.status == 304:
            cached_body = cached_state.get("body") if cached_state else None
            if isinstance(cached_body, bytes):
                cached_entries, corrupt = _parse_feed(feed, cached_body)
                if not corrupt:
                    return cached_entries
            logger.warning("Cached feed body is unavailable or corrupt: %s", feed.url)
            response = _download_feed(client, feed.url)
    except DownloadError as e:
        logger.warning("Failed to fetch feed '%s' (%s): %s", feed.title, feed.url, e)
        return []

    entries, corrupt = _parse_feed(feed, response.body)
    if corrupt:
        logger.warning("Downloaded feed body could not be parsed: %s", feed.url)
        return entries

    if on_cache_update is not None:
        on_cache_update(
            {
                "configured_url": feed.url,
                "final_url": response.final_url,
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "body": response.body,
                "fetched_at": datetime.now(timezone.utc),
            }
        )

    return entries


def _download_feed(
    client: HttpClient, url: str, headers: Optional[Mapping[str, str]] = None
):
    if headers:
        return client.get(
            url,
            accepted_content_types=_FEED_CONTENT_TYPES,
            allow_missing_content_type=True,
            headers=headers,
        )
    return client.get(
        url,
        accepted_content_types=_FEED_CONTENT_TYPES,
        allow_missing_content_type=True,
    )


def _parse_feed(
    feed: FeedConfig, response_content: bytes
) -> tuple[List[FeedEntry], bool]:
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
    return entries, bool(getattr(parsed, "bozo", False) and not entries)


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
