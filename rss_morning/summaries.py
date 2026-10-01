"""Integration with OpenRouter for article summaries."""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

from bs4 import BeautifulSoup

from .openrouter import complete_structured

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "exec-summary": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Executive summary of the articles",
        },
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the article being summarized",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category of the article",
                    },
                    "summary": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Generated title",
                            },
                            "rank-reasoning": {
                                "type": "string",
                                "description": "Why this article was ranked highly",
                            },
                            "what": {
                                "type": "string",
                                "description": "The What summary",
                            },
                            "so-what": {
                                "type": "string",
                                "description": "The So What? Summary",
                            },
                            "now-what": {
                                "type": "string",
                                "description": "The Now What? Section",
                            },
                        },
                        "required": [
                            "title",
                            "rank-reasoning",
                            "what",
                            "so-what",
                            "now-what",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": ["url", "category", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["exec-summary", "summaries"],
    "additionalProperties": False,
}


def sanitize_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text()


def build_summary_input(articles: list[dict]) -> str:
    """Prepare request payload from article data."""
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


def generate_summary(
    articles: list[dict],
    system_prompt: str,
    return_dict: bool = False,
    batch_size: int = 100,
    dry_run: bool = False,
    model: Optional[str] = None,
) -> str | Tuple[str, Optional[dict]]:
    """Generate summary JSON for a list of articles using OpenRouter."""
    if not articles:
        logger.info(
            "No articles available for summarisation; returning empty summary list."
        )
        empty = {"summaries": []}
        if return_dict:
            return json.dumps(empty, ensure_ascii=False), empty
        return json.dumps(empty, ensure_ascii=False)

    combined_summaries = []
    exec_summaries = []

    # Process articles in batches
    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        logger.info(
            "Processing summarization batch %d of %d (size: %d)",
            (i // batch_size) + 1,
            (len(articles) + batch_size - 1) // batch_size,
            len(batch),
        )

        try:
            summary_input = build_summary_input(batch)

            if dry_run:
                logger.info(
                    "DRY RUN: Prepared payload for batch %d: %s",
                    (i // batch_size) + 1,
                    summary_input,
                )
                continue

            parsed = complete_structured(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary_input},
                ],
                json_schema=SUMMARY_SCHEMA,
                schema_name="rss_summaries",
                model=model,
            )

            batch_summaries = parsed.get("summaries", [])
            exec_summary = parsed.get("exec-summary")
            if exec_summary:
                exec_summaries.extend(exec_summary)

            logger.info("Got %d summaries from batch", len(batch_summaries))
            combined_summaries.extend(batch_summaries)

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to generate summary for batch starting at index %d: %s", i, exc
            )
            continue

    if dry_run:
        logger.info("DRY RUN: skipping API call.")
        mock_resp = {"dry_run": True}
        if return_dict:
            return json.dumps(mock_resp), mock_resp
        return json.dumps(mock_resp)

    # Post-processing / Sanitization on the combined result
    for item in combined_summaries:
        if "summary" in item:
            item["summary"]["title"] = sanitize_html(item["summary"].get("title"))
            item["summary"]["what"] = sanitize_html(item["summary"].get("what"))
            item["summary"]["so-what"] = sanitize_html(item["summary"].get("so-what"))
            item["summary"]["now-what"] = sanitize_html(item["summary"].get("now-what"))
        if "category" in item:
            item["category"] = sanitize_html(item["category"])

    # Final Combined Output
    final_obj = {"summaries": combined_summaries}
    if exec_summaries:
        final_obj["exec_summary"] = "\n".join(exec_summaries)

    rendered = json.dumps(final_obj, ensure_ascii=False, indent=2)

    if not combined_summaries and articles:
        logger.warning("No summaries were generated from any batch.")

    if return_dict:
        return rendered, final_obj
    return rendered
