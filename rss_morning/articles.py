"""Article retrieval and content processing using Trafilatura."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import trafilatura

logger = logging.getLogger(__name__)


@dataclass
class ArticleContent:
    """Structured content retrieved from an article."""

    text: Optional[str]
    image: Optional[str]


def fetch_article_content(
    url: str, timeout: int = 20, extractor: Optional[str] = None
) -> ArticleContent:
    """Download article content using Trafilatura and return text and lead image."""
    logger.debug("Downloading article content from %s using trafilatura", url)
    content = _fetch_with_trafilatura(url)

    if content.image:
        content.image = urljoin(url, content.image)

    return content


def _fetch_with_trafilatura(url: str) -> ArticleContent:
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            logger.warning("Trafilatura failed to download content for %s", url)
            return ArticleContent(text=None, image=None)

        text = trafilatura.extract(downloaded, include_comments=False)
        metadata = trafilatura.extract_metadata(downloaded)
        image = metadata.image if metadata else None

        if not text:
            logger.info("Article contains no readable text: %s", url)

        return ArticleContent(text=text, image=image)

    except Exception as exc:
        logger.warning(
            "Unexpected error while processing article %s with trafilatura: %s",
            url,
            exc,
        )
        return ArticleContent(text=None, image=None)


def truncate_text(value: str, limit: int = 1000) -> str:
    """Limit text length to the given character limit."""
    if not value or not limit or len(value) <= limit:
        return value
    logger.debug("Truncating article text from %d to %d characters", len(value), limit)
    chunk = value[:limit]
    if " " in chunk:
        chunk = chunk.rsplit(" ", 1)[0]
    return chunk
