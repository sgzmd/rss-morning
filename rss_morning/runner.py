"""High-level orchestration for the rss_morning application."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import concurrent.futures
from typing import Any, List, Optional

from .articles import fetch_article_content, truncate_text
from .config import parse_feeds_config
from .emailing import send_email_report
from .feeds import fetch_feed_entries, select_recent_entries
from .summaries import generate_summary
from . import db

logger = logging.getLogger(__name__)


@dataclass
class RunConfig:
    """Runtime options for executing the application."""

    feeds_file: str
    limit: int
    max_age_hours: Optional[float]
    summary: bool
    pre_filter: bool = False
    pre_filter_embeddings_path: Optional[str] = None
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    email_subject: Optional[str] = None
    cluster_threshold: float = 0.84
    save_articles_path: Optional[str] = None
    load_articles_path: Optional[str] = None
    max_article_length: int = 5000
    system_prompt: Optional[str] = None
    extractor: str = "newspaper"
    concurrency: int = 10
    database_enabled: bool = False
    database_connection_string: Optional[str] = None


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


def _collect_entries(config: RunConfig, session_factory=None) -> List[dict]:
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

    def process_feed(feed):
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

    logger.info("Fetching article text for %d selected entries", len(unique_entries))

    output = []

    def process_entry(entry):
        try:
            if session_factory:
                with session_factory() as session:
                    cached = db.get_article(session, entry.link)
                    if cached:
                        logger.debug("Cache hit for %s", entry.link)
                        return {
                            "url": cached["url"],
                            "category": entry.category,
                            "title": cached["title"],
                            "summary": cached["summary"] or entry.summary or "",
                            "text": cached["text"],
                            "image": cached["image"],
                        }

            content = fetch_article_content(entry.link, extractor=config.extractor)
            payload = {
                "url": entry.link,
                "category": entry.category,
                "title": entry.title,
                "summary": entry.summary or "",
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
        future_to_entry = {
            executor.submit(process_entry, entry): entry for entry in unique_entries
        }
        for future in concurrent.futures.as_completed(future_to_entry):
            res = future.result()
            if res:
                output.append(res)

    logger.info("Completed processing. Outputting %d articles as JSON.", len(output))
    return output


def _attach_summary_images(summary_payload: Any, source_articles: List[dict]) -> Any:
    """Populate missing image fields in summary payload using original articles."""
    if not isinstance(summary_payload, dict):
        return summary_payload

    summaries = summary_payload.get("summaries")
    if not isinstance(summaries, list) or not summaries:
        return summary_payload

    images_by_url = {
        article.get("url"): article.get("image")
        for article in source_articles
        if article.get("url") and article.get("image")
    }
    if not images_by_url:
        return summary_payload

    for item in summaries:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        current_image = item.get("image")
        if current_image:
            continue
        replacement = images_by_url.get(url)
        if replacement:
            item["image"] = replacement

    return summary_payload


def _build_default_email_subject() -> str:
    timestamp = datetime.now(timezone.utc)
    return "RSS Mailer update for " + timestamp.strftime("%Y-%m-%d at %H:%M")


def execute(config: RunConfig) -> RunResult:
    """Run the application logic and return the result payload."""
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
        articles = _collect_entries(config, session_factory=session_factory)

    if config.save_articles_path:
        _save_articles_to_file(config.save_articles_path, articles)

    if config.pre_filter:
        logger.info("Applying embedding pre-filter to %d articles", len(articles))

        from .prefilter import EmbeddingArticleFilter

        # Create a config object with the runtime settings.
        emb_config_cls = type(EmbeddingArticleFilter.CONFIG)
        emb_config = emb_config_cls(
            model=EmbeddingArticleFilter.CONFIG.model,
            batch_size=EmbeddingArticleFilter.CONFIG.batch_size,
            threshold=EmbeddingArticleFilter.CONFIG.threshold,
            max_article_length=config.max_article_length,
        )

        filter_layer = EmbeddingArticleFilter(
            query_embeddings_path=config.pre_filter_embeddings_path,
            config=emb_config,
            session_factory=session_factory,
        )
        filtered_articles = filter_layer.filter(
            list(articles), cluster_threshold=config.cluster_threshold
        )
        if filtered_articles is None:
            logger.warning(
                "Embedding pre-filter returned no articles; keeping original set."
            )
        else:
            logger.info(
                "Embedding pre-filter retained %d of %d articles",
                len(filtered_articles),
                len(articles),
            )
            articles = filtered_articles

    email_payload: Any = articles
    is_summary_payload = False

    if config.summary:
        if not config.system_prompt:
            logger.warning(
                "Summary requested but no system prompt provided using default."
            )
            # We might want to fail here or provide a hardcoded default,
            # but for now let's assume the caller ensures it or we might need a default string.
            # Actually, `generate_summary` expects a string now.
            # Let's fail if it's missing to be safe, or provide a minimal one.
            raise ValueError("Summary requested but no system_prompt configured.")

        summary_output, summary_data = generate_summary(
            articles, config.system_prompt, return_dict=True
        )
        output_text = summary_output
        if summary_data is not None:
            summary_data = _attach_summary_images(summary_data, articles)
            output_text = json.dumps(summary_data, indent=2, ensure_ascii=False)
            email_payload = summary_data
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
