"""High-level orchestration for the rss_morning application."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import concurrent.futures
from typing import Any, List, Optional, Tuple, cast

from .articles import fetch_article_content, prepare_tokenizer, truncate_text
from .config import parse_feeds_config
from .emailing import send_email_report
from .feeds import fetch_feed_entries, select_recent_entries
from .http_client import HttpClient
from .summaries import SummarySettings, generate_summary
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
    pre_filter_mode: str = "full-text"
    candidate_multiplier: int = 3
    max_cluster_size: int = 5
    pre_filter_embeddings_path: Optional[str] = None
    pre_filter_queries_file: Optional[str] = None
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    email_subject: Optional[str] = None
    cluster_threshold: float = 0.84
    save_articles_path: Optional[str] = None
    load_articles_path: Optional[str] = None
    max_article_length: int = 100
    system_prompt: Optional[str] = None
    extractor: str = "newspaper"
    concurrency: int = 20
    database_enabled: bool = False
    database_connection_string: Optional[str] = None
    embedding_provider: str = "fastembed"
    embedding_model: str = "intfloat/multilingual-e5-large"
    llm_provider: str = "openrouter"
    llm_model: str = "bytedance-seed/seed-2.0-mini"
    llm_fallback_models: tuple[str, ...] = (
        "z-ai/glm-4.7-flash",
        "openai/gpt-4o-mini",
    )
    llm_routing: str = "price"
    llm_max_input_price_per_million: float = 0.20
    llm_max_output_price_per_million: float = 0.75
    llm_require_structured_output: bool = True
    llm_max_batch_articles: int = 20
    llm_max_input_tokens: int = 30_000
    llm_request_timeout_seconds: float = 90
    llm_max_attempts: int = 3
    llm_max_split_depth: int = 6
    llm_cache_enabled: bool = True
    llm_dry_run: bool = False
    http_connect_timeout: float = 5
    http_read_timeout: float = 20
    max_feed_bytes: int = 5_242_880
    max_article_bytes: int = 10_485_760
    http_retries: int = 2
    http_backoff_seconds: float = 0.5
    http_per_host_concurrency: int = 2


@dataclass
class RunResult:
    """Returned data after executing the app."""

    output_text: str
    email_payload: Any
    is_summary: bool


def _create_prefilter(config: RunConfig, session_factory=None):
    from .prefilter import EmbeddingArticleFilter

    emb_config_cls = type(EmbeddingArticleFilter.CONFIG)
    emb_config = emb_config_cls(
        model=config.embedding_model,
        provider=config.embedding_provider,
        batch_size=EmbeddingArticleFilter.CONFIG.batch_size,
        threshold=EmbeddingArticleFilter.CONFIG.threshold,
        max_article_length=config.max_article_length,
        max_cluster_size=config.max_cluster_size,
    )
    return EmbeddingArticleFilter(
        query_embeddings_path=config.pre_filter_embeddings_path,
        queries_file=config.pre_filter_queries_file,
        config=emb_config,
        session_factory=session_factory,
    )


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
    feed_cache_states = {}
    feed_cache_updates = []
    feed_cache_updates_lock = threading.Lock()

    if session_factory:
        try:
            with session_factory() as session:
                feed_cache_states = db.get_feed_http_states(
                    session, [feed.url for feed in feeds]
                )
        except Exception:
            logger.exception(
                "Bulk feed HTTP cache read failed; fetching unconditionally"
            )

    def record_feed_cache_update(state):
        with feed_cache_updates_lock:
            feed_cache_updates.append(state)

    feed_http_client = HttpClient(
        connect_timeout=config.http_connect_timeout,
        read_timeout=config.http_read_timeout,
        max_bytes=config.max_feed_bytes,
        retries=config.http_retries,
        backoff_seconds=config.http_backoff_seconds,
        per_host_concurrency=config.http_per_host_concurrency,
    )
    article_http_client = HttpClient(
        connect_timeout=config.http_connect_timeout,
        read_timeout=config.http_read_timeout,
        max_bytes=config.max_article_bytes,
        retries=config.http_retries,
        backoff_seconds=config.http_backoff_seconds,
        per_host_concurrency=config.http_per_host_concurrency,
    )

    def process_feed(feed):
        try:
            entries = fetch_feed_entries(
                feed,
                http_client=feed_http_client,
                cached_state=feed_cache_states.get(feed.url),
                on_cache_update=record_feed_cache_update if session_factory else None,
            )
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

    if session_factory and feed_cache_updates:
        try:
            with session_factory() as session:
                db.upsert_feed_http_states(session, feed_cache_updates)
        except Exception:
            logger.exception(
                "Bulk feed HTTP cache write failed; continuing without cache"
            )

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
    prepare_tokenizer()

    output = []
    raw_cache_payloads = []
    cached_articles = {}
    cache_read_failures = set()

    if session_factory:
        urls = [entry.link for entry in unique_entries]
        try:
            with session_factory() as session:
                cached_articles = db.get_articles(session, urls)
        except Exception:
            logger.exception(
                "Bulk article cache read failed; checking entries individually"
            )
            for entry in unique_entries:
                try:
                    with session_factory() as session:
                        cached = db.get_article(session, entry.link)
                    if cached:
                        cached_articles[entry.link] = cached
                except Exception:
                    logger.exception("Failed to read article cache for %s", entry.link)
                    cache_read_failures.add(entry.link)

    if config.pre_filter and config.pre_filter_mode == "metadata-first":
        metadata_articles = []
        for entry in unique_entries:
            cached = cached_articles.get(entry.link)
            payload = {
                "url": entry.link,
                "category": entry.category,
                "title": cached.get("title") if cached else entry.title,
                "summary": (cached.get("summary") if cached else None)
                or entry.summary
                or "",
                "published": entry.published.isoformat() if entry.published else None,
            }
            if cached and cached.get("text") is not None:
                payload["text"] = cached["text"]
            metadata_articles.append(payload)
        try:
            selector = _create_prefilter(config, session_factory)
            selected_metadata = selector.select_metadata_candidates(
                metadata_articles,
                candidate_multiplier=config.candidate_multiplier,
            )
            selected_urls = {str(item.get("url")) for item in selected_metadata}
            unique_entries = [
                entry for entry in unique_entries if entry.link in selected_urls
            ]
            logger.info(
                "Metadata-first stage retained %d of %d entries for page download",
                len(unique_entries),
                len(metadata_articles),
            )
        except Exception:
            logger.exception(
                "Metadata-first prefilter failed; using full-text compatibility path"
            )

    def process_entry(entry):
        try:
            if entry.link in cache_read_failures:
                return None

            cached = cached_articles.get(entry.link)
            cached_text = cached.get("text") if cached else None
            if cached and cached_text is not None:
                logger.debug("Cache hit for %s", entry.link)
                return {
                    "url": cached["url"],
                    "category": entry.category,
                    "title": cached["title"],
                    "summary": cached["summary"] or entry.summary or "",
                    "text": truncate_text(cached_text, limit=config.max_article_length),
                    "image": cached["image"],
                    "published": cached["published"].isoformat()
                    if cached.get("published")
                    else None,
                }

            content = fetch_article_content(
                entry.link,
                extractor=config.extractor,
                http_client=article_http_client,
            )
            raw_payload = {
                "url": entry.link,
                "category": entry.category,
                "title": entry.title,
                "summary": entry.summary or "",
                "published": entry.published.isoformat() if entry.published else None,
            }
            if content.text:
                raw_payload["text"] = content.text
            else:
                logger.info(
                    "Article text unavailable; including metadata only: %s", entry.link
                )
            if content.image:
                raw_payload["image"] = content.image

            if session_factory and content.text:
                raw_cache_payloads.append(raw_payload)

            output_payload = dict(raw_payload)
            if content.text:
                output_payload["text"] = truncate_text(
                    content.text, limit=config.max_article_length
                )
            return output_payload
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

    if session_factory and raw_cache_payloads:
        try:
            with session_factory() as session:
                db.upsert_articles(session, raw_cache_payloads)
        except Exception:
            logger.exception(
                "Bulk article cache write failed; continuing without cache"
            )

    logger.info("Completed processing. Outputting %d articles as JSON.", len(output))
    # Sort by published date descending (newest first)
    output.sort(key=lambda x: x.get("published") or "", reverse=True)
    # Then sort by category ascending (stable sort preserves published order within category)
    output.sort(key=lambda x: x.get("category") or "")
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
    if config.max_article_length <= 0:
        raise ValueError("max_article_length must be positive")

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

        filter_layer = _create_prefilter(config, session_factory)
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
            articles = cast(List[dict], filtered_articles)

    email_payload: Any = articles
    is_summary_payload = False

    if config.summary:
        if not config.system_prompt:
            logger.warning(
                "Summary requested but no system prompt provided using default."
            )
            raise ValueError("Summary requested but no system_prompt configured.")

        cache_get = None
        cache_put = None
        if session_factory and config.llm_cache_enabled:

            def cache_get(identity: str) -> Optional[str]:
                with session_factory() as session:
                    return db.get_llm_batch_cache(session, identity)

            def cache_put(identity: str, payload: str) -> None:
                with session_factory() as session:
                    db.upsert_llm_batch_cache(session, identity, payload)

        summary_settings = SummarySettings(
            provider=config.llm_provider,
            model=config.llm_model,
            fallback_models=config.llm_fallback_models,
            routing=config.llm_routing,
            max_input_price_per_million=config.llm_max_input_price_per_million,
            max_output_price_per_million=config.llm_max_output_price_per_million,
            require_structured_output=config.llm_require_structured_output,
            max_batch_articles=config.llm_max_batch_articles,
            max_input_tokens=config.llm_max_input_tokens,
            request_timeout_seconds=config.llm_request_timeout_seconds,
            max_attempts=config.llm_max_attempts,
            max_split_depth=config.llm_max_split_depth,
            cache_enabled=config.llm_cache_enabled,
        )
        summary_output, summary_data = cast(
            Tuple[str, Optional[dict]],
            generate_summary(
                articles,
                config.system_prompt,
                return_dict=True,
                dry_run=config.llm_dry_run,
                settings=summary_settings,
                cache_get=cache_get,
                cache_put=cache_put,
            ),
        )
        output_text = summary_output

        if config.llm_dry_run:
            logger.info("LLM dry run completed. Exiting without sending email.")
            return RunResult(
                output_text=output_text, email_payload=None, is_summary=True
            )

        if summary_data is not None:
            summary_data = _attach_summary_images(summary_data, articles)
            if isinstance(summary_data, dict) and "summaries" in summary_data:
                summary_data["summaries"].sort(key=lambda x: x.get("category") or "")
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
