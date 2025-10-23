"""Integration with Gemini for article summaries."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency optional unless summaries requested
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None
    genai_types = None


def load_system_prompt(path: str) -> str:
    """Load the Gemini system prompt from disk."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            prompt = handle.read().strip()
            logger.debug("Loaded system prompt from %s", path)
            return prompt
    except FileNotFoundError:
        logger.error("System prompt file not found: %s", path)
        raise


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


def call_gemini(system_prompt: str, payload: str) -> str:
    """Call the Gemini API and return the raw response text."""
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


def generate_summary(
    articles: list[dict], prompt_path: str = "prompt.md", return_dict: bool = False
) -> str | Tuple[str, Optional[dict]]:
    """Generate summary JSON for a list of articles."""
    if not articles:
        logger.info("No articles available for summarisation; returning empty summary list.")
        empty = {"summaries": []}
        if return_dict:
            return json.dumps(empty, ensure_ascii=False), empty
        return json.dumps(empty, ensure_ascii=False)

    system_prompt = load_system_prompt(prompt_path)
    payload = build_summary_input(articles)

    try:
        raw_response = call_gemini(system_prompt, payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to generate summary via Gemini API: %s", exc)
        logger.debug("Falling back to raw article JSON output.")
        fallback = json.dumps(articles, ensure_ascii=False, indent=2)
        if return_dict:
            return fallback, None
        return fallback

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        logger.debug("Gemini response not valid JSON; attempting to clean response...")
        lines = raw_response.splitlines()
        cleaned = "\n".join(lines[1:-1])
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Gemini response was not valid JSON; returning raw response text.")
            if return_dict:
                return raw_response, None
            return raw_response

    logger.debug("Successfully generated summary JSON via Gemini API.")
    rendered = json.dumps(parsed, ensure_ascii=False, indent=2)
    if return_dict:
        return rendered, parsed
    return rendered
