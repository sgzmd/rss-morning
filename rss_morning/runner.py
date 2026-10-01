"""High-level orchestration for the rss_morning application."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

from .articles import fetch_article_content, truncate_text
from .classify import ClassificationDecision, classify_entry
from .config import parse_feeds_config
from .emailing import send_email_report
from .feeds import fetch_feed_entries, select_recent_entries
from .models import FeedConfig, FeedEntry
from .digest import generate_digest
from . import db

logger = logging.getLogger(__name__)


@dataclass
class RunConfig:
    """Runtime options for executing the application."""

    feeds_file: str
    limit: int
    max_age_hours: Optional[float]
    summary: bool
    classify: bool = True
    classify_model: str = "jev-latest"
    classify_threshold: float = 0.50
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    email_subject: Optional[str] = None
    save_articles_path: Optional[str] = None
    load_articles_path: Optional[str] = None
    max_article_length: int = 100
    system_prompt: Optional[str] = None
    extractor: str = "newspaper"
    concurrency: int = 20
    database_enabled: bool = False
    database_connection_string: Optional[str] = None
    llm_dry_run: bool = False
    llm_model: Optional[str] = None
    pre_filter: Optional[bool] = None

    def __post_init__(self):
        if self.pre_filter is not None:
            self.classify = self.pre_filter


@dataclass
class RunResult:
    """Returned data after executing the app."""

    output_text: str
    email_payload: Any
    is_summary: bool


def _load_articles_from_file(path: str) -> List[dict]:
    location = Path(path)
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Article snapshot not found: {location}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Article snapshot is not valid JSON: {location}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("Article snapshot must contain a JSON array.")

    articles: List[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            raise RuntimeError("Article snapshot must contain objects only.")
        articles.append(dict(item))

    logger.info("Loaded %d articles from %s", len(articles), location)
    return articles


def _save_articles_to_file(path: str, articles: List[dict]) -> None:
    location = Path(path)
    if location.parent and not location.parent.exists():
        location.parent.mkdir(parents=True, exist_ok=True)

    serialisable = [dict(article) for article in articles]
    location.write_text(
        json.dumps(serialisable, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Saved %d articles to %s", len(serialisable), location)


def _collect_feed_entries(config: RunConfig) -> List[FeedEntry]:
    """Fetch all configured feeds and return sorted, deduplicated FeedEntries."""
    feeds = parse_feeds_config(config.feeds_file)
    if not feeds:
        raise RuntimeError("No feeds found in the configuration.")

    cutoff: Optional[datetime] = None
    if config.max_age_hours is not None:
        if config.max_age_hours <= 0:
            raise ValueError("--max-age-hours must be positive.")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=config.max_age_hours)
        logger.info("Applying article cutoff: newer than %s", cutoff)

    selected_entries = []
    any_entries_fetched = False

    def process_feed(feed: FeedConfig) -> List[FeedEntry]:
        try:
            entries = fetch_feed_entries(feed)
            if not entries:
                logger.info("No entries retrieved for feed %s", feed.url)
                return []

            per_feed_entries = select_recent_entries(entries, config.limit, cutoff)
            logger.info(
                "Selected %d entries for feed %s", len(per_feed_entries), feed.url
            )
            return per_feed_entries
        except Exception:
            logger.exception("Failed to process feed %s", feed.url)
            return []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=config.concurrency
    ) as executor:
        future_to_feed = {executor.submit(process_feed, feed): feed for feed in feeds}
        for future in concurrent.futures.as_completed(future_to_feed):
            entries = future.result()
            if entries:
                any_entries_fetched = True
                selected_entries.extend(entries)

    if not any_entries_fetched:
        raise RuntimeError("No entries were retrieved from the configured feeds.")

    # Ensure consistent ordering and deduplicate across feeds by URL.
    sorted_entries = sorted(
        selected_entries, key=lambda item: item.published, reverse=True
    )
    unique_entries = []
    seen_links = set()
    for entry in sorted_entries:
        if entry.link in seen_links:
            continue
        unique_entries.append(entry)
        seen_links.add(entry.link)

    logger.info("Collected %d unique entries from feeds", len(unique_entries))
    return unique_entries


def _extract_candidate_articles(
    candidates: List[tuple[FeedEntry, Optional[ClassificationDecision]]],
    config: RunConfig,
    session_factory=None,
) -> List[dict]:
    """Extract article body text ONLY for selected candidates."""
    logger.info("Fetching article text for %d selected candidates", len(candidates))
    output = []

    def process_candidate(
        candidate_pair: tuple[FeedEntry, Optional[ClassificationDecision]],
    ) -> Optional[dict]:
        entry, decision = candidate_pair
        category = decision.primary_area if decision else entry.category
        try:
            if session_factory:
                with session_factory() as session:
                    cached = db.get_article(session, entry.link)
                    if cached:
                        logger.debug("Cache hit for %s", entry.link)
                        return {
                            "url": cached["url"],
                            "category": category,
                            "primary_area": category,
                            "relevance_probability": decision.relevance_probability
                            if decision
                            else None,
                            "title": cached["title"],
                            "summary": cached["summary"] or entry.summary or "",
                            "text": truncate_text(
                                cached["text"], limit=config.max_article_length
                            ),
                            "image": cached["image"],
                            "published": cached["published"].isoformat()
                            if cached.get("published")
                            else None,
                        }

            content = fetch_article_content(entry.link)
            payload = {
                "url": entry.link,
                "category": category,
                "primary_area": category,
                "relevance_probability": decision.relevance_probability
                if decision
                else None,
                "title": entry.title,
                "summary": entry.summary or "",
                "published": entry.published.isoformat() if entry.published else None,
            }
            if content.text:
                payload["text"] = truncate_text(
                    content.text, limit=config.max_article_length
                )
            else:
                logger.info(
                    "Article text unavailable; including metadata only: %s", entry.link
                )
            if content.image:
                payload["image"] = content.image

            if session_factory:
                with session_factory() as session:
                    db.upsert_article(session, payload)

            return payload
        except Exception:
            logger.exception("Failed to process article content for %s", entry.link)
            return None

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=config.concurrency
    ) as executor:
        futures = [executor.submit(process_candidate, c) for c in candidates]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                output.append(res)

    logger.info("Completed extraction of %d articles.", len(output))
    # Sort by published date descending (newest first)
    output.sort(key=lambda x: x.get("published") or "", reverse=True)
    # Then sort by category ascending
    output.sort(key=lambda x: x.get("category") or "")
    return output


def _collect_entries(config: RunConfig, session_factory=None) -> List[dict]:
    """Compatibility wrapper: collect and extract without pre-filtering."""
    entries = _collect_feed_entries(config)
    candidates = [(entry, None) for entry in entries]
    return _extract_candidate_articles(
        candidates, config, session_factory=session_factory
    )


def _build_default_email_subject() -> str:
    today_str = datetime.now(timezone.utc).strftime("%d %B %Y")
    return f"Morning RSS Digest - {today_str}"


def execute(config: RunConfig) -> RunResult:
    """Run the pipeline: collect -> exact dedupe -> Jev classify -> extract -> summarise -> output."""
    session_factory = None
    if config.database_enabled:
        if not config.database_connection_string:
            logger.warning(
                "Database enabled but no connection string provided. Caching disabled."
            )
        else:
            engine = db.init_engine(config.database_connection_string)
            if engine:
                session_factory = db.get_session_factory(engine)

    if config.load_articles_path:
        articles = _load_articles_from_file(config.load_articles_path)
    else:
        # Step 1: Collect and deduplicate feed entries (metadata only)
        entries = _collect_feed_entries(config)

        # Step 2: Jev classify on cheap metadata
        if config.classify:
            logger.info(
                "Classifying %d feed entries with Jev (System One)", len(entries)
            )
            candidates = []
            for entry in entries:
                metadata = {
                    "title": entry.title,
                    "summary": entry.summary,
                    "category": entry.category,
                    "link": entry.link,
                }
                decision = classify_entry(
                    metadata,
                    model=config.classify_model,
                    threshold=config.classify_threshold,
                )
                if decision.is_plausible:
                    logger.info(
                        "Jev KEEP (prob=%.2f, area=%s): %s",
                        decision.relevance_probability,
                        decision.primary_area,
                        entry.title,
                    )
                    candidates.append((entry, decision))
                else:
                    logger.debug(
                        "Jev DROP (prob=%.2f, area=%s): %s",
                        decision.relevance_probability,
                        decision.primary_area,
                        entry.title,
                    )
            logger.info(
                "Jev classification retained %d of %d entries",
                len(candidates),
                len(entries),
            )
        else:
            candidates = [(entry, None) for entry in entries]

        # Step 3: Extract full article text ONLY for plausible candidates
        articles = _extract_candidate_articles(
            candidates, config, session_factory=session_factory
        )

    if config.save_articles_path:
        _save_articles_to_file(config.save_articles_path, articles)

    email_payload: Any = articles
    is_summary_payload = False

    if config.summary:
        edition_data = generate_digest(
            articles,
            system_prompt=config.system_prompt,
            dry_run=config.llm_dry_run,
            model=config.llm_model,
        )
        output_text = json.dumps(edition_data, indent=2, ensure_ascii=False)

        if config.llm_dry_run:
            logger.info("LLM dry run completed. Exiting without sending email.")
            return RunResult(
                output_text=output_text, email_payload=None, is_summary=True
            )

        email_payload = edition_data
        is_summary_payload = True
    else:
        output_text = json.dumps(articles, indent=2, ensure_ascii=False)

    if config.email_to:
        subject = config.email_subject or _build_default_email_subject()
        send_email_report(
            payload=email_payload,
            is_summary=is_summary_payload,
            to_address=config.email_to,
            from_address=config.email_from,
            subject=subject,
        )

    return RunResult(
        output_text=output_text,
        email_payload=email_payload,
        is_summary=is_summary_payload,
    )
