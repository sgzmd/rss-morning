"""LLM integrations for structured article summaries."""

from __future__ import annotations

import importlib
import json
import logging
import os
from typing import Any

from bs4 import BeautifulSoup
from openai import OpenAI

try:
    genai: Any = importlib.import_module("google.genai")
    types: Any = importlib.import_module("google.genai.types")
except Exception:  # pragma: no cover - optional dependency
    genai = None
    types = None

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["exec-summary", "summaries"],
    "properties": {
        "exec-summary": {
            "type": "array",
            "items": {"type": "string"},
        },
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url", "category", "summary"],
                "properties": {
                    "url": {"type": "string"},
                    "category": {"type": "string"},
                    "summary": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "title",
                            "rank-reasoning",
                            "what",
                            "so-what",
                            "now-what",
                        ],
                        "properties": {
                            "title": {"type": "string"},
                            "rank-reasoning": {"type": "string"},
                            "what": {"type": "string"},
                            "so-what": {"type": "string"},
                            "now-what": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def sanitize_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text()


def build_summary_input(articles: list[dict]) -> str:
    """Prepare the LLM request payload from article data."""
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
    provider: str = "gemini",
    model: str | None = None,
) -> str | tuple[str, dict | None]:
    """Generate summary JSON for a list of articles."""
    if not articles:
        logger.info(
            "No articles available for summarisation; returning empty summary list."
        )
        empty: dict = {"summaries": []}
        if return_dict:
            return json.dumps(empty, ensure_ascii=False), empty
        return json.dumps(empty, ensure_ascii=False)

    provider = provider.strip().lower()
    if provider not in {"gemini", "openrouter"}:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    resolved_model = model or (
        "gemini-flash-latest" if provider == "gemini" else "mistralai/mistral-nemo"
    )

    client: Any
    if provider == "gemini":
        if genai is None or types is None:
            raise RuntimeError(
                "google-genai package is required for Gemini summaries but is not installed."
            )
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key and not dry_run:
            raise RuntimeError(
                "GOOGLE_API_KEY or GEMINI_API_KEY is required for Gemini summaries."
            )
        client = genai.Client(api_key=api_key)
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key and not dry_run:
            raise RuntimeError(
                "OPENROUTER_API_KEY is required for OpenRouter summaries."
            )
        client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key or "dry-run")

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

            # Construct input with system prompt and articles
            input_text = f"{system_prompt}\n\n{summary_input}"
            if provider == "gemini":
                logger.debug("Gemini request payload: %s", input_text)
            else:
                logger.debug("OpenRouter request payload: %s", input_text)

            if dry_run:
                logger.info(
                    "DRY RUN: Prepared payload for batch %d.",
                    (i // batch_size) + 1,
                )
                logger.debug("DRY RUN: LLM request payload: %s", input_text)
                continue

            if provider == "gemini":
                contents: Any = [
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=input_text),
                        ],
                    ),
                ]
                generate_content_config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=types.Schema(
                        type=types.Type.OBJECT,
                        description="Top-level response structure expected from the LLM.",
                        required=["summaries"],
                        properties={
                            "exec-summary": types.Schema(
                                type=types.Type.ARRAY,
                                items=types.Schema(
                                    type=types.Type.STRING,
                                    description="Executive summary of the articles",
                                ),
                            ),
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
                                            required=[
                                                "title",
                                                "rank-reasoning",
                                                "what",
                                                "so-what",
                                                "now-what",
                                            ],
                                            properties={
                                                "title": types.Schema(
                                                    type=types.Type.STRING,
                                                    description="Generated title",
                                                ),
                                                "rank-reasoning": types.Schema(
                                                    type=types.Type.STRING,
                                                    description="Why this article was ranked highly",
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

                response_text = ""
                # Accumulate stream to return full JSON string
                for chunk in client.models.generate_content_stream(
                    model=resolved_model,
                    contents=contents,
                    config=generate_content_config,
                ):
                    if chunk.text:
                        response_text += chunk.text
            else:
                response = client.chat.completions.create(
                    model=resolved_model,
                    messages=[{"role": "user", "content": input_text}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "rss_morning_summary",
                            "strict": True,
                            "schema": SUMMARY_JSON_SCHEMA,
                        },
                    },
                    temperature=0,
                    extra_body={"provider": {"require_parameters": True}},
                )
                response_text = response.choices[0].message.content or ""

            if provider == "gemini":
                logger.debug("Gemini response text: %s", response_text)
            else:
                logger.debug("OpenRouter response text: %s", response_text)

            # Parse JSON
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                raise ValueError("LLM response must be a JSON object")

            batch_summaries = parsed.get("summaries", [])
            if not isinstance(batch_summaries, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("summary"), dict)
                for item in batch_summaries
            ):
                raise ValueError("LLM summaries must be a list of summary objects")

            exec_summary = parsed.get("exec-summary")
            if exec_summary is not None and (
                not isinstance(exec_summary, list)
                or any(not isinstance(item, str) for item in exec_summary)
            ):
                raise ValueError("LLM exec-summary must be a list of strings")
            if exec_summary:
                exec_summaries.extend(exec_summary)

            logger.info("Got %d summaries from batch", len(batch_summaries))
            combined_summaries.extend(batch_summaries)

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to generate summary for batch starting at index %d: %s", i, exc
            )
            continue

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
    final_obj: dict = {"summaries": combined_summaries}
    if exec_summaries:
        final_obj["exec_summary"] = "\n".join(exec_summaries)

    rendered = json.dumps(final_obj, ensure_ascii=False, indent=2)

    if dry_run:
        logger.info("DRY RUN: skipping API call.")
        mock_resp = {"dry_run": True}
        if return_dict:
            return json.dumps(mock_resp), mock_resp
        return json.dumps(mock_resp)

    if not combined_summaries and articles:
        logger.warning("No summaries were generated from any batch.")

    if return_dict:
        return rendered, final_obj
    return rendered
