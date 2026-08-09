"""Article retrieval and content processing."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

from newspaper import Article, Config
from newspaper.article import ArticleException
import trafilatura

import tiktoken

logger = logging.getLogger(__name__)

_ENCODER_UNINITIALIZED = object()
_encoder: Any = _ENCODER_UNINITIALIZED
_encoder_lock = threading.Lock()


def _get_encoder() -> Any | None:
    """Return the shared encoder, or ``None`` when offline initialization fails."""
    global _encoder
    if _encoder is _ENCODER_UNINITIALIZED:
        with _encoder_lock:
            if _encoder is _ENCODER_UNINITIALIZED:
                try:
                    _encoder = tiktoken.get_encoding("cl100k_base")
                except Exception as exc:  # noqa: BLE001 - tokenizer boundary
                    logger.warning(
                        "Token encoder initialization failed (%s); using offline "
                        "character fallback.",
                        type(exc).__name__,
                    )
                    _encoder = None
    return _encoder


def prepare_tokenizer() -> None:
    """Initialize the shared tokenizer before concurrent article workers start."""
    _get_encoder()


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
        content = _fetch_with_trafilatura(url)
    else:
        content = _fetch_with_newspaper(url, timeout)

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


def truncate_text(value: str, limit: int = 100) -> str:
    """Limit text length to the given number of tokens."""
    if not isinstance(value, str):
        raise TypeError("text must be a string")
    if limit <= 0:
        raise ValueError("limit must be positive")

    encoder = _get_encoder()
    if encoder is None:
        return value if len(value) <= limit else value[:limit]

    tokens = encoder.encode(value)
    if len(tokens) <= limit:
        return value
    logger.debug("Truncating article text from %d to %d tokens", len(tokens), limit)
    return encoder.decode(tokens[:limit])
