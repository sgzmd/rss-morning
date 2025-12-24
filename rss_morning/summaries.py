"""Integration with Gemini for article summaries."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Tuple

from bs4 import BeautifulSoup

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional dependency
    genai = None
    types = None

logger = logging.getLogger(__name__)


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
    return payload


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

    if genai is None or types is None:
        raise RuntimeError(
            "google-genai package is required for --summary but is not installed."
        )

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    logger.info("Using API key ending with: %s", api_key[:])
    client = genai.Client(api_key=api_key)

    model = "gemini-flash-latest"
    summary_input = build_summary_input(articles)

    # Construct input with system prompt and articles
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=f"{system_prompt}\n\n{summary_input}"),
            ],
        ),
    ]

    generate_content_config = types.GenerateContentConfig(
        # thinking_config=types.ThinkingConfig(
        #     thinking_level="HIGH",
        # ),
        response_mime_type="application/json",
        response_schema=types.Schema(
            type=types.Type.OBJECT,
            description="Top-level response structure expected from the LLM.",
            required=["summaries"],
            properties={
                "summaries": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        required=["url", "category", "summary"],
                        properties={
                            "url": types.Schema(
                                type=types.Type.STRING,
                                description="URL of the article being summarized",
                            ),
                            "category": types.Schema(
                                type=types.Type.STRING,
                                description="Category of the article",
                            ),
                            "summary": types.Schema(
                                type=types.Type.OBJECT,
                                description="Fields describing the summary content.",
                                required=["title", "what", "so-what", "now-what"],
                                properties={
                                    "title": types.Schema(
                                        type=types.Type.STRING,
                                        description="Generated title",
                                    ),
                                    "what": types.Schema(
                                        type=types.Type.STRING,
                                        description="The What summary",
                                    ),
                                    "so-what": types.Schema(
                                        type=types.Type.STRING,
                                        description="The So What? Summary",
                                    ),
                                    "now-what": types.Schema(
                                        type=types.Type.STRING,
                                        description="The Now What? Section",
                                    ),
                                },
                            ),
                        },
                    ),
                ),
            },
        ),
    )

    logger.info("Requesting summary from Gemini API (model %s)", model)

    try:
        response_text = ""
        # Accumulate stream to return full JSON string
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            if chunk.text:
                response_text += chunk.text

        # Parse JSON
        parsed = json.loads(response_text)

        # Sanitize content
        for item in parsed.get("summaries", []):
            if "summary" in item:
                item["summary"]["title"] = sanitize_html(item["summary"].get("title"))
                item["summary"]["what"] = sanitize_html(item["summary"].get("what"))
                item["summary"]["so-what"] = sanitize_html(
                    item["summary"].get("so-what")
                )
                item["summary"]["now-what"] = sanitize_html(
                    item["summary"].get("now-what")
                )
            if "category" in item:
                item["category"] = sanitize_html(item["category"])

        rendered = json.dumps(parsed, ensure_ascii=False, indent=2)
        if return_dict:
            return rendered, parsed
        return rendered

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to generate summary via Gemini API: %s", exc)
        logger.debug("Falling back to raw article JSON output.")
        fallback = json.dumps(articles, ensure_ascii=False, indent=2)
        if return_dict:
            return fallback, None
        return fallback
