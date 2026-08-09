"""Article retrieval and content processing."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin

from newspaper import Article, Config
from newspaper.article import ArticleException
import trafilatura

import tiktoken

from .http_client import HttpClient

logger = logging.getLogger(__name__)
_DEFAULT_HTTP_CLIENT = HttpClient(max_bytes=10_485_760)

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
    base_url: Optional[str] = field(default=None, repr=False, compare=False)


def fetch_article_content(
    url: str,
    timeout: int = 20,
    extractor: str = "newspaper",
    http_client: Optional[HttpClient] = None,
) -> ArticleContent:
    """Download article content using selected extractor and return text and lead image."""
    logger.debug("Downloading article content from %s using %s", url, extractor)

    if extractor == "trafilatura":
        content = _fetch_with_trafilatura(url, http_client)
    else:
        content = _fetch_with_newspaper(url, timeout, http_client)

    if content.image:
        content.image = urljoin(content.base_url or url, content.image)

    return content


def _download_html(url: str, http_client: Optional[HttpClient]) -> tuple[str, str]:
    response = (http_client or _DEFAULT_HTTP_CLIENT).get(
        url,
        accepted_content_types=("text/html", "application/xhtml+xml"),
        allow_missing_content_type=True,
    )
    return response.body.decode("utf-8", errors="replace"), response.final_url


def _fetch_with_trafilatura(
    url: str, http_client: Optional[HttpClient] = None
) -> ArticleContent:
    try:
        downloaded, final_url = _download_html(url, http_client)

        text = trafilatura.extract(downloaded, include_comments=False)

        metadata = trafilatura.extract_metadata(downloaded)
        image = metadata.image if metadata else None

        if not text:
            logger.info("Article contains no readable text: %s", url)

        return ArticleContent(text=text, image=image, base_url=final_url)

    except Exception as exc:  # noqa: BLE001 - extractor and HTTP boundary
        logger.warning(
            "Unexpected error while processing article %s with trafilatura: %s",
            url,
            exc,
        )
        return ArticleContent(text=None, image=None)


def _fetch_with_newspaper(
    url: str, timeout: int, http_client: Optional[HttpClient] = None
) -> ArticleContent:
    config = Config()
    config.fetch_images = True
    config.memoize_articles = False
    config.request_timeout = timeout

    try:
        downloaded, final_url = _download_html(url, http_client)
        article = Article(url=final_url, config=config)
        article.set_html(downloaded)
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

    return ArticleContent(text=text, image=image, base_url=final_url)


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
