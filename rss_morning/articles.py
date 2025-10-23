"""Article retrieval and content processing."""

from __future__ import annotations

import logging
from typing import Optional

import requests
from lxml import html
from readability import Document

logger = logging.getLogger(__name__)


def fetch_article_text(url: str, timeout: int = 20) -> Optional[str]:
    """Download and extract readable text from an article URL."""
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


def truncate_text(value: str, limit: int = 1000) -> str:
    """Limit text length to the given number of characters."""
    if len(value) <= limit:
        return value
    logger.debug("Truncating article text to %d characters", limit)
    return value[:limit]
