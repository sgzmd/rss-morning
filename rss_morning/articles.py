"""Article retrieval and content processing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from newspaper import Article, Config
from newspaper.article import ArticleException

logger = logging.getLogger(__name__)


@dataclass
class ArticleContent:
    """Structured content retrieved from an article."""

    text: Optional[str]
    image: Optional[str]


def fetch_article_content(url: str, timeout: int = 20) -> ArticleContent:
    """Download article content using newspaper3k and return text and lead image."""
    logger.debug("Downloading article content from %s", url)
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
