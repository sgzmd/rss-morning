import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional
from xml.etree import ElementTree as ET

import feedparser
import requests
from lxml import html
from readability import Document

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - dependency optional unless summaries requested
    genai = None
    genai_types = None


logger = logging.getLogger(__name__)


@dataclass
class FeedConfig:
    category: str
    title: str
    url: str


@dataclass
class FeedEntry:
    link: str
    category: str
    title: str
    published: datetime
    summary: Optional[str] = None


def parse_feeds_config(path: str) -> List[FeedConfig]:
    logger.info("Loading feed configuration from %s", path)
    tree = ET.parse(path)
    root = tree.getroot()
    body = root.find("body")
    feeds: List[FeedConfig] = []

    def walk(outline: ET.Element, current_category: Optional[str]) -> None:
        title = outline.attrib.get("title") or outline.attrib.get("text")
        feed_url = outline.attrib.get("xmlUrl")
        outline_type = outline.attrib.get("type")
        children = list(outline.findall("outline"))

        if outline_type == "rss" and feed_url:
            feeds.append(
                FeedConfig(
                    category=current_category or title or "Uncategorized",
                    title=title or feed_url,
                    url=feed_url,
                )
            )
            logger.debug("Registered feed '%s' (category='%s')", feed_url, feeds[-1].category)
            return

        # Treat any outline that contains children as a category container.
        next_category = title if title else current_category
        for child in children:
            walk(child, next_category)

    if body is None:
        raise ValueError("feeds.xml is missing the <body> section.")

    for outline in body.findall("outline"):
        walk(outline, outline.attrib.get("title") or outline.attrib.get("text"))

    logger.info("Loaded %d feed endpoints from configuration", len(feeds))
    return feeds


def to_datetime(value: Optional[time.struct_time]) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)


def fetch_feed_entries(feed: FeedConfig) -> List[FeedEntry]:
    logger.info("Fetching feed '%s' (%s)", feed.title, feed.url)
    parsed = feedparser.parse(feed.url)
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
            content = getattr(entry, "content", None)
            if content:
                try:
                    summary = content[0].get("value")
                except (TypeError, KeyError, IndexError, AttributeError):
                    summary = None
        if summary:
            summary = summary.strip()

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

    logger.info(
        "Collected %d entries from feed '%s'", len(entries), feed.url
    )
    return entries


def fetch_article_text(url: str, timeout: int = 20) -> Optional[str]:
    logger.debug("Downloading article content from %s", url)
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to download article %s: %s", url, exc)
        return None

    document = Document(response.text)
    summary_html = document.summary(html_partial=True)
    try:
        parsed = html.fromstring(summary_html)
    except (html.ParserError, TypeError) as exc:
        logger.warning("Failed to parse article HTML %s: %s", url, exc)
        return None

    text = parsed.text_content().strip()
    if not text:
        logger.info("Article contains no readable text: %s", url)
        return None

    return text


def select_recent_entries(
    entries: Iterable[FeedEntry],
    limit: int,
    cutoff: Optional[datetime] = None,
) -> List[FeedEntry]:
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

    logger.info("Selected %d unique recent entries (requested %d)", len(unique_entries), limit)
    return unique_entries


def truncate_text(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    logger.debug("Truncating article text to %d characters", limit)
    return value[:limit]


def load_system_prompt(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            prompt = handle.read().strip()
            logger.debug("Loaded system prompt from %s", path)
            return prompt
    except FileNotFoundError:
        logger.error("System prompt file not found: %s", path)
        raise


def build_summary_input(articles: List[dict]) -> str:
    prepared = []
    for index, article in enumerate(articles, start=1):
        prepared.append(
            {
                "id": f"article-{index}",
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "summary": article.get("summary", ""),
                "content": article.get("text", "") or "",
                "category": article.get("category", ""),
            }
        )
    payload = json.dumps(prepared, ensure_ascii=False, indent=2)
    logger.debug("Prepared %d articles for summarisation", len(prepared))
    return payload


def call_gemini(system_prompt: str, payload: str) -> str:
    if genai is None:
        raise RuntimeError(
            "google-genai package is required for --summary but is not installed."
        )

    client = genai.Client()
    logger.info("Requesting summary from Gemini API (model gemini-2.5-flash)")
    response = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=payload,
        config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
    )
    if not hasattr(response, "text") or response.text is None:
        raise RuntimeError("Gemini API returned no text response.")
    return response.text.strip()


def generate_summary(articles: List[dict], prompt_path: str = "prompt.md") -> str:
    if not articles:
        logger.info("No articles available for summarisation; returning empty summary list.")
        return json.dumps({"summaries": []}, ensure_ascii=False)

    system_prompt = load_system_prompt(prompt_path)
    payload = build_summary_input(articles)

    try:
        raw_response = call_gemini(system_prompt, payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to generate summary via Gemini API: %s", exc)
        logger.debug("Falling back to raw article JSON output.")
        return json.dumps(articles, ensure_ascii=False, indent=2)

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        logger.warning("Gemini response was not valid JSON; returning raw response text.")
        return raw_response

    logger.debug("Successfully generated summary JSON via Gemini API.")
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch recent articles from configured RSS feeds.")
    parser.add_argument("-n", "--limit", type=int, default=10, help="Number of articles to fetch.")
    parser.add_argument(
        "--feeds-file", default="feeds.xml", help="Path to the OPML file that defines the feeds."
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
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(log_level, int):
        parser.error(f"Unsupported log level: {args.log_level}")

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.debug("Logger initialised with level %s", args.log_level.upper())

    feeds = parse_feeds_config(args.feeds_file)
    if not feeds:
        logger.error("No feeds found in the configuration.")
        sys.exit(1)

    cutoff: Optional[datetime] = None
    if args.max_age_hours is not None:
        if args.max_age_hours <= 0:
            parser.error("--max-age-hours must be positive.")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.max_age_hours)
        logger.info("Applying article cutoff: newer than %s", cutoff)

    feed_entries: List[FeedEntry] = []
    for feed in feeds:
        try:
            entries = fetch_feed_entries(feed)
            feed_entries.extend(entries)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process feed %s", feed.url)

    if not feed_entries:
        logger.error("No entries were retrieved from the configured feeds.")
        sys.exit(1)

    selected_entries = select_recent_entries(feed_entries, args.limit, cutoff)
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
    if args.summary:
        summary_output = generate_summary(output)
        print(summary_output)
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
