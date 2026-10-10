"""High-level orchestration for the rss_morning application."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .articles import fetch_article_content, truncate_text
from .classify import ClassificationDecision, classify_entry
from .config import DigestConfig, parse_feeds_config
from .digest import generate_digest
from .emailing import send_email_report
from .feeds import fetch_feed_entries, select_recent_entries
from .models import AreaConfig, FeedConfig, FeedEntry, TechnologyFootprint

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
    relevance_instructions: Optional[str] = None
    relevance_criteria_true: Optional[str] = None
    relevance_criteria_false: Optional[str] = None
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    email_subject: Optional[str] = None
    save_articles_path: Optional[str] = None
    load_articles_path: Optional[str] = None
    max_article_length: int = 1500
    system_prompt: Optional[str] = None
    concurrency: int = 20
    llm_dry_run: bool = False
    llm_model: Optional[str] = None
    reasoning_effort: Optional[str] = "low"
    digest_config: Optional[DigestConfig] = None
    areas: Dict[str, AreaConfig] = field(default_factory=dict)
    technologies: Optional[TechnologyFootprint] = None
    tenant_id: Optional[str] = None
    profile: str = "security"
    title: Optional[str] = None
    subtitle: Optional[str] = None
    timezone: Optional[str] = None
    extractor: str = "trafilatura"
    grounding_rules: Optional[str] = None
    holdings_file: Optional[str] = None


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
        text = (item.get("text") or "").strip()
        if not text:
            logger.info(
                "Skipping article without text content from snapshot: %s",
                item.get("url") or item.get("title"),
            )
            continue
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


def _classify_entries(
    entries: List[FeedEntry], config: RunConfig
) -> List[tuple[FeedEntry, Optional[ClassificationDecision]]]:
    """Filter feed entries using Jev System One metadata classification."""
    if not config.classify:
        return [(entry, None) for entry in entries]

    logger.info("Classifying %d feed entries with Jev (System One)", len(entries))
    candidates = []
    rel_crit = None
    if config.relevance_criteria_true or config.relevance_criteria_false:
        rel_crit = {}
        if config.relevance_criteria_true:
            rel_crit["true"] = config.relevance_criteria_true
        if config.relevance_criteria_false:
            rel_crit["false"] = config.relevance_criteria_false

    for entry in entries:
        metadata = {
            "title": entry.title,
            "summary": entry.summary,
            "category": entry.category,
            "link": entry.link,
        }
        classify_kwargs: Dict[str, Any] = {}
        if config.areas:
            classify_kwargs["areas"] = config.areas
        if config.relevance_instructions:
            classify_kwargs["relevance_instructions"] = config.relevance_instructions
        if rel_crit:
            classify_kwargs["relevance_criteria"] = rel_crit

        import inspect

        sig = inspect.signature(classify_entry)
        has_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if "profile" in sig.parameters or has_kwargs:
            classify_kwargs["profile"] = config.profile

        decision = classify_entry(
            metadata,
            model=config.classify_model,
            threshold=config.classify_threshold,
            **classify_kwargs,
        )
        if decision.is_plausible:
            logger.info(
                "Jev KEEP (prob=%.2f >= %.2f, area=%s): %s",
                decision.relevance_probability,
                decision.effective_threshold,
                decision.primary_area,
                entry.title,
            )
            candidates.append((entry, decision))
        else:
            logger.debug(
                "Jev DROP (prob=%.2f < %.2f, area=%s): %s",
                decision.relevance_probability,
                decision.effective_threshold,
                decision.primary_area,
                entry.title,
            )

    logger.info(
        "Jev classification retained %d of %d entries", len(candidates), len(entries)
    )
    return candidates


def _extract_candidate_articles(
    candidates: List[tuple[FeedEntry, Optional[ClassificationDecision]]],
    config: RunConfig,
) -> List[dict]:
    """Extract article body text ONLY for selected candidates, excluding articles without content."""
    logger.info("Fetching article text for %d selected candidates", len(candidates))
    output = []
    extract_limit = (
        config.digest_config.editor_article_chars
        if config.digest_config
        else config.max_article_length
    )

    def process_candidate(
        candidate_pair: tuple[FeedEntry, Optional[ClassificationDecision]],
    ) -> Optional[dict]:
        entry, decision = candidate_pair
        category = decision.primary_area if decision else entry.category
        try:
            content = fetch_article_content(entry.link)
            extracted_text = (content.text or "").strip()
            if not extracted_text:
                logger.info(
                    "Article text unavailable or empty; excluding article from digest: %s",
                    entry.link,
                )
                return None

            payload = {
                "url": entry.link,
                "category": category,
                "primary_area": category,
                "relevance_probability": (
                    decision.relevance_probability if decision else None
                ),
                "title": entry.title,
                "summary": entry.summary or "",
                "published": entry.published.isoformat() if entry.published else None,
                "text": truncate_text(extracted_text, limit=extract_limit),
            }
            if content.image:
                payload["image"] = content.image

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


def _collect_entries(config: RunConfig) -> List[dict]:
    """Compatibility wrapper: collect and extract without pre-filtering."""
    entries = _collect_feed_entries(config)
    candidates = [(entry, None) for entry in entries]
    return _extract_candidate_articles(candidates, config)


def _build_default_email_subject(
    tz_name: Optional[str] = None, title: Optional[str] = None
) -> str:
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            now = datetime.now(timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    today_str = now.strftime("%d %B %Y")
    prefix = title or "Morning RSS Digest"
    return f"{prefix} - {today_str}"


def execute(config: RunConfig) -> RunResult:
    """Run the linear pipeline: collect -> dedupe -> Jev -> extract -> digest -> output."""
    # Optional structured holdings context
    holdings_context = None
    if config.holdings_file:
        from .holdings import format_holdings_context, load_holdings

        holdings_data = load_holdings(config.holdings_file)
        holdings_context = format_holdings_context(holdings_data)

    if config.load_articles_path:
        articles = _load_articles_from_file(config.load_articles_path)
    else:
        entries = _collect_feed_entries(config)
        candidates = _classify_entries(entries, config)
        articles = _extract_candidate_articles(candidates, config)

    if config.save_articles_path:
        _save_articles_to_file(config.save_articles_path, articles)

    date_str = None
    if config.timezone:
        try:
            from zoneinfo import ZoneInfo

            date_str = datetime.now(ZoneInfo(config.timezone)).strftime("%B %d, %Y")
        except Exception:
            pass

    def _resolve_subject() -> str:
        if config.email_subject:
            return config.email_subject
        import inspect

        sig = inspect.signature(_build_default_email_subject)
        if len(sig.parameters) == 0:
            return _build_default_email_subject()
        return _build_default_email_subject(config.timezone, config.title)

    if not config.summary:
        output_text = json.dumps(articles, indent=2, ensure_ascii=False)
        if config.llm_dry_run:
            logger.info("LLM dry run completed. Exiting without sending email.")
            return RunResult(
                output_text=output_text, email_payload=None, is_summary=False
            )

        if config.email_to:
            email_kwargs = {"areas": config.areas} if config.areas else {}
            email_kwargs["profile"] = config.profile
            email_kwargs["title"] = config.title
            email_kwargs["subtitle"] = config.subtitle
            email_kwargs["date_str"] = date_str
            sent = send_email_report(
                payload=articles,
                is_summary=False,
                to_address=config.email_to,
                from_address=config.email_from,
                subject=_resolve_subject(),
                **email_kwargs,
            )
            if sent is False:
                raise RuntimeError(
                    f"Email delivery failed for recipient: {config.email_to}"
                )
        return RunResult(
            output_text=output_text, email_payload=articles, is_summary=False
        )

    digest_kwargs: Dict[str, Any] = {}
    if config.areas:
        digest_kwargs["areas"] = config.areas
    if config.digest_config:
        digest_kwargs["digest_config"] = config.digest_config
    if config.reasoning_effort:
        digest_kwargs["reasoning_effort"] = config.reasoning_effort
    if config.technologies:
        digest_kwargs["technologies"] = config.technologies
    if config.profile:
        digest_kwargs["profile"] = config.profile
    if config.grounding_rules:
        digest_kwargs["grounding_rules"] = config.grounding_rules
    if holdings_context:
        digest_kwargs["holdings_context"] = holdings_context

    edition_data = generate_digest(
        articles,
        system_prompt=config.system_prompt,
        dry_run=config.llm_dry_run,
        model=config.llm_model,
        **digest_kwargs,
    )
    output_text = json.dumps(edition_data, indent=2, ensure_ascii=False)

    if config.llm_dry_run:
        logger.info("LLM dry run completed. Exiting without sending email.")
        return RunResult(output_text=output_text, email_payload=None, is_summary=True)

    if config.email_to:
        email_kwargs = {"areas": config.areas} if config.areas else {}
        email_kwargs["profile"] = config.profile
        email_kwargs["title"] = config.title
        email_kwargs["subtitle"] = config.subtitle
        email_kwargs["date_str"] = date_str
        sent = send_email_report(
            payload=edition_data,
            is_summary=True,
            to_address=config.email_to,
            from_address=config.email_from,
            subject=_resolve_subject(),
            **email_kwargs,
        )
        if sent is False:
            raise RuntimeError(
                f"Email delivery failed for recipient: {config.email_to}"
            )

    return RunResult(
        output_text=output_text, email_payload=edition_data, is_summary=True
    )
