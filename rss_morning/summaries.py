"""Integration with Gemini for article summaries."""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple, List

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:  # pragma: no cover - optional dependency
    genai = None
    genai_types = None

logger = logging.getLogger(__name__)


class SummaryFields(BaseModel):
    """Fields describing the summary content."""

    title: str = Field(description="Generated title")
    what: str = Field(description="The What summary")
    so_what: str = Field(alias="so-what", description="The So What? Summary")
    now_what: str = Field(alias="now-what", description="The Now What? Section")


class ArticleSummary(BaseModel):
    """Structured summary for a single article."""

    url: str = Field(description="URL of the article being summarized")
    summary: SummaryFields
    category: str = Field(description="Category of the article")


class SummaryResponse(BaseModel):
    """Top-level response structure expected from the LLM."""

    summaries: List[ArticleSummary]


def sanitize_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text()


def build_summary_input(articles: list[dict]) -> str:
    """Prepare Gemini request payload from article data."""
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
    logger.debug("Summary input payload: \n%s", payload)
    return payload


def call_gemini(system_prompt: str, payload: str) -> str:
    """Call the Gemini API and return the raw response text."""
    if genai is None or genai_types is None:
        raise RuntimeError(
            "google-genai package is required for --summary but is not installed."
        )

    client = genai.Client()
    logger.info("Requesting summary from Gemini API (model gemini-flash-lite-latest)")

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=payload,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=SummaryResponse.model_json_schema(),
        ),
    )
    if not hasattr(response, "text") or response.text is None:
        raise RuntimeError("Gemini API returned no text response.")
    return response.text.strip()


def generate_summary(
    articles: list[dict], system_prompt: str, return_dict: bool = False
) -> str | Tuple[str, Optional[dict]]:
    """Generate summary JSON for a list of articles."""
    if not articles:
        logger.info(
            "No articles available for summarisation; returning empty summary list."
        )
        empty = {"summaries": []}
        if return_dict:
            return json.dumps(empty, ensure_ascii=False), empty
        return json.dumps(empty, ensure_ascii=False)

    payload = build_summary_input(articles)

    try:
        raw_response = call_gemini(system_prompt, payload)

        # Validate with Pydantic
        summary_response = SummaryResponse.model_validate_json(raw_response)

        # Sanitize content
        for item in summary_response.summaries:
            item.summary.title = sanitize_html(item.summary.title)
            item.summary.what = sanitize_html(item.summary.what)
            item.summary.so_what = sanitize_html(item.summary.so_what)
            item.summary.now_what = sanitize_html(item.summary.now_what)
            item.category = sanitize_html(item.category)

        # Convert back to dict for consistency with rest of app
        parsed = summary_response.model_dump(by_alias=True)

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to generate summary via Gemini API: %s", exc)
        logger.debug("Falling back to raw article JSON output.")
        fallback = json.dumps(articles, ensure_ascii=False, indent=2)
        if return_dict:
            return fallback, None
        return fallback

    logger.debug("Successfully generated summary JSON via Gemini API.")
    rendered = json.dumps(parsed, ensure_ascii=False, indent=2)
    if return_dict:
        return rendered, parsed
    return rendered
