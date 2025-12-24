"""Article retrieval and content processing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from newspaper import Article, Config
from newspaper.article import ArticleException
import trafilatura

logger = logging.getLogger(__name__)


@dataclass
class ArticleContent:
    """Structured content retrieved from an article."""

    text: Optional[str]
    image: Optional[str]


def fetch_article_content(
    url: str, timeout: int = 20, extractor: str = "newspaper"
) -> ArticleContent:
    """Download article content using selected extractor and return text and lead image."""
    logger.debug("Downloading article content from %s using %s", url, extractor)

    if extractor == "trafilatura":
        return _fetch_with_trafilatura(url)

    return _fetch_with_newspaper(url, timeout)


def _fetch_with_trafilatura(url: str) -> ArticleContent:
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            logger.warning("Trafilatura failed to download content for %s", url)
            return ArticleContent(text=None, image=None)

        # Extract content; include_images=True might be needed if we want to try parsing images
        # but trafilatura is text-focused. We can try to get metadata for image.
        # trafilatura.extract returns a string (text) or None, or a dict if output_format="json" (but that returns JSON string)
        # Let's use bare extract for text, and maybe extract_metadata for image.
        text = trafilatura.extract(downloaded, include_comments=False)

        # For image, we can try to extract metadata
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


def _fetch_with_newspaper(url: str, timeout: int) -> ArticleContent:
    config = Config()
    config.fetch_images = True
    config.memoize_articles = False
    config.request_timeout = timeout

    article = Article(url=url, config=config)

    try:
        article.download()
        article.parse()
    except ArticleException as exc:
        logger.warning("Failed to process article %s: %s", url, exc)
        return ArticleContent(text=None, image=None)
    except Exception as exc:  # noqa: BLE001 - defensive against library internals
        logger.warning("Unexpected error while processing article %s: %s", url, exc)
        return ArticleContent(text=None, image=None)

    text = (article.text or "").strip() or None
    image = (article.top_image or "").strip() or None

    if not text:
        logger.info("Article contains no readable text: %s", url)

    return ArticleContent(text=text, image=image)


def truncate_text(value: str, limit: int = 1000) -> str:
    """Limit text length to the given number of characters."""
    if len(value) <= limit:
        return value
    logger.debug("Truncating article text to %d characters", limit)
    return value[:limit]
