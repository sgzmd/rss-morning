"""Configuration loading for RSS feeds."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .models import FeedConfig
from .summaries import (
    DEFAULT_OPENROUTER_FALLBACKS,
    DEFAULT_OPENROUTER_MODEL,
    SummarySettings,
)

logger = logging.getLogger(__name__)


@dataclass
class PreFilterConfig:
    enabled: bool = False
    mode: str = "full-text"
    candidate_multiplier: int = 3
    max_cluster_size: int = 5
    embeddings_path: Optional[str] = None
    cluster_threshold: float = 0.8
    queries_file: Optional[str] = None


@dataclass
class EmbeddingsConfig:
    provider: str = "fastembed"
    model: str = "intfloat/multilingual-e5-large"


LLMConfig = SummarySettings


@dataclass
class EmailConfig:
    to_addr: Optional[str] = None
    from_addr: Optional[str] = None
    subject: Optional[str] = None


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = None


@dataclass
class DatabaseConfig:
    enabled: bool = False
    connection_string: Optional[str] = None


@dataclass
class HttpConfig:
    connect_timeout: float = 5
    read_timeout: float = 20
    max_feed_bytes: int = 5_242_880
    max_article_bytes: int = 10_485_760
    retries: int = 2
    backoff_seconds: float = 0.5
    per_host_concurrency: int = 2


@dataclass
class AppConfig:
    feeds_file: str
    env_file: Optional[str]
    limit: int = 10
    max_age_hours: Optional[float] = None
    summary: bool = False
    pre_filter: PreFilterConfig = field(default_factory=PreFilterConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    llm: SummarySettings = field(default_factory=SummarySettings)
    email: EmailConfig = field(default_factory=EmailConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    prompt: Optional[str] = None
    max_article_length: int = 100
    extractor: str = "newspaper"
    concurrency: int = 10


def parse_feeds_config(path: str) -> List[FeedConfig]:
    """Parse the OPML configuration file and return feed definitions."""
    logger.info("Loading feed configuration from %s", path)
    tree = ET.parse(path)
    root = tree.getroot()
    body = root.find("body")
    feeds: List[FeedConfig] = []

    def walk(outline: ET.Element, current_category: Optional[str]) -> None:
        title = outline.attrib.get("title") or outline.attrib.get("text")
        feed_url = outline.attrib.get("xmlUrl")
        outline_type = outline.attrib.get("type")
        children = list(outline.findall("outline"))

        if outline_type == "rss" and feed_url:
            feeds.append(
                FeedConfig(
                    category=current_category or title or "Uncategorized",
                    title=title or feed_url,
                    url=feed_url,
                )
            )
            logger.debug(
                "Registered feed '%s' (category='%s')", feed_url, feeds[-1].category
            )
            return

        next_category = title if title else current_category
        for child in children:
            walk(child, next_category)

    if body is None:
        raise ValueError("feeds.xml is missing the <body> section.")

    for outline in body.findall("outline"):
        walk(outline, outline.attrib.get("title") or outline.attrib.get("text"))

    logger.info("Loaded %d feed endpoints from configuration", len(feeds))
    return feeds


def _resolve_path(base_path: Path, target_path: str) -> str:
    """Resolve a path relative to the base config file if it's not absolute."""
    target = Path(target_path)
    if target.is_absolute():
        return str(target)
    return str((base_path.parent / target).resolve())


def parse_env_config(path: str) -> Dict[str, str]:
    """Parse environment variables from XML."""
    env_vars: Dict[str, str] = {}
    if not path:
        return env_vars

    logger.info("Loading environment configuration from %s", path)
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        for var in root.findall("variable"):
            name = var.attrib.get("name")
            value = var.text
            if name and value:
                env_vars[name] = value.strip()
    except Exception as exc:
        logger.warning("Failed to load environment config: %s", exc)
        raise

    return env_vars


def _raw(parent: ET.Element, name: str, default: str) -> str:
    value = parent.findtext(name)
    return default if value is None else value.strip()


def _invalid(path: str, value: str, expectation: str) -> ValueError:
    safe_value = value if value else "empty"
    return ValueError(f"Invalid <{path}> value {safe_value!r}; expected {expectation}")


def _boolean(parent: ET.Element, name: str, default: bool, path: str) -> bool:
    raw = _raw(parent, name, str(default).lower()).lower()
    if raw not in {"true", "false"}:
        raise _invalid(path, raw, "true or false")
    return raw == "true"


def _integer(
    parent: ET.Element, name: str, default: int, path: str, *, minimum: int
) -> int:
    raw = _raw(parent, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise _invalid(path, raw, f"an integer >= {minimum}") from None
    if value < minimum:
        raise _invalid(path, raw, f"an integer >= {minimum}")
    return value


def _number(
    parent: ET.Element,
    name: str,
    default: float,
    path: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    raw = _raw(parent, name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise _invalid(path, raw, "a finite number") from None
    if (
        not math.isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        bounds = (
            f"between {minimum} and {maximum}"
            if maximum is not None
            else f">= {minimum}"
        )
        raise _invalid(path, raw, f"a finite number {bounds}")
    return value


def _choice(
    parent: ET.Element, name: str, default: str, path: str, choices: set[str]
) -> str:
    raw = _raw(parent, name, default).lower()
    if raw not in choices:
        raise _invalid(path, raw, "one of " + ", ".join(sorted(choices)))
    return raw


def _nonempty(parent: ET.Element, name: str, default: str, path: str) -> str:
    raw = _raw(parent, name, default)
    if not raw:
        raise _invalid(path, raw, "a nonempty string")
    return raw


_KNOWN_OPENROUTER_PRICES = {
    "bytedance-seed/seed-2.0-mini": (0.10, 0.40),
    "z-ai/glm-4.7-flash": (0.06, 0.40),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "google/gemini-2.5-flash": (0.30, 2.50),
}


def _parse_llm(root: ET.Element) -> SummarySettings:
    node = root.find("llm")
    if node is None:
        return SummarySettings()
    provider = _choice(
        node, "provider", "openrouter", "llm/provider", {"openrouter", "gemini"}
    )
    default_model = (
        "gemini-flash-latest" if provider == "gemini" else DEFAULT_OPENROUTER_MODEL
    )
    model = _nonempty(node, "model", default_model, "llm/model")
    fallback_node = node.find("fallback-models")
    fallbacks: tuple[str, ...]
    if fallback_node is None:
        fallbacks = DEFAULT_OPENROUTER_FALLBACKS if provider == "openrouter" else ()
    else:
        fallbacks = tuple(
            _nonempty(item, ".", "", "llm/fallback-models/model")
            for item in fallback_node.findall("model")
        )
    if provider == "openrouter":
        fallback_models = fallbacks
        models = (model, *fallback_models)
        if len(fallback_models) > 3:
            raise _invalid(
                "llm/fallback-models", str(len(fallback_models)), "zero to three models"
            )
        for index, value in enumerate(models):
            if (
                value.count("/") != 1
                or not all(value.split("/"))
                or any(character.isspace() for character in value)
            ):
                model_path = "llm/model" if index == 0 else "llm/fallback-models/model"
                raise _invalid(
                    model_path, value, "a nonempty OpenRouter provider/model slug"
                )
        if len(set(models)) != len(models):
            raise _invalid("llm/fallback-models", "duplicate", "unique model slugs")
    elif fallbacks:
        raise _invalid("llm/fallback-models", str(list(fallbacks)), "empty for Gemini")
    settings = SummarySettings(
        provider=provider,
        model=model,
        fallback_models=fallbacks,
        routing=_choice(
            node, "routing", "price", "llm/routing", {"price", "throughput", "latency"}
        ),
        max_input_price_per_million=_number(
            node,
            "max-input-price-per-million",
            0.20,
            "llm/max-input-price-per-million",
            minimum=0.000000001,
        ),
        max_output_price_per_million=_number(
            node,
            "max-output-price-per-million",
            0.75,
            "llm/max-output-price-per-million",
            minimum=0.000000001,
        ),
        require_structured_output=_boolean(
            node, "require-structured-output", True, "llm/require-structured-output"
        ),
        max_batch_articles=_integer(
            node, "max-batch-articles", 20, "llm/max-batch-articles", minimum=1
        ),
        max_input_tokens=_integer(
            node, "max-input-tokens", 30_000, "llm/max-input-tokens", minimum=1
        ),
        request_timeout_seconds=_number(
            node,
            "request-timeout-seconds",
            90,
            "llm/request-timeout-seconds",
            minimum=0.000000001,
        ),
        max_attempts=_integer(node, "max-attempts", 3, "llm/max-attempts", minimum=1),
        max_split_depth=_integer(
            node, "max-split-depth", 6, "llm/max-split-depth", minimum=0
        ),
        cache_enabled=_boolean(node, "cache-enabled", True, "llm/cache-enabled"),
    )
    for configured_model in (settings.model, *settings.fallback_models):
        known = _KNOWN_OPENROUTER_PRICES.get(configured_model)
        if known and (
            known[0] > settings.max_input_price_per_million
            or known[1] > settings.max_output_price_per_million
        ):
            logger.warning(
                "Configured <llm/model> %s exceeds a known price cap; runtime max_price remains authoritative",
                configured_model,
            )
    return settings


def parse_app_config(path: str) -> AppConfig:
    """Parse and fully validate the main application configuration XML."""
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    logger.info("Loading application configuration from %s", config_path)
    root = ET.parse(config_path).getroot()

    feeds_node = root.find("feeds")
    if feeds_node is None or not feeds_node.text or not feeds_node.text.strip():
        raise ValueError("Config missing <feeds> path")
    feeds_file = _resolve_path(config_path, feeds_node.text.strip())
    env_node = root.find("env")
    env_file = (
        _resolve_path(config_path, env_node.text.strip())
        if env_node is not None and env_node.text and env_node.text.strip()
        else None
    )
    limit = _integer(root, "limit", 10, "limit", minimum=1)
    max_age_node = root.find("max-age-hours")
    max_age_hours = (
        _number(root, "max-age-hours", 1, "max-age-hours", minimum=0.000000001)
        if max_age_node is not None
        else None
    )
    summary = _boolean(root, "summary", False, "summary")
    max_len = _integer(root, "max-article-length", 100, "max-article-length", minimum=1)
    extractor = _choice(
        root, "extractor", "newspaper", "extractor", {"newspaper", "trafilatura"}
    )
    concurrency = _integer(root, "concurrency", 10, "concurrency", minimum=1)

    pf_node = root.find("pre-filter")
    pre_filter = PreFilterConfig()
    if pf_node is not None:
        pre_filter.enabled = _boolean(pf_node, "enabled", False, "pre-filter/enabled")
        pre_filter.mode = _choice(
            pf_node,
            "mode",
            "full-text",
            "pre-filter/mode",
            {"full-text", "metadata-first"},
        )
        pre_filter.candidate_multiplier = _integer(
            pf_node,
            "candidate-multiplier",
            3,
            "pre-filter/candidate-multiplier",
            minimum=1,
        )
        pre_filter.max_cluster_size = _integer(
            pf_node, "max-cluster-size", 5, "pre-filter/max-cluster-size", minimum=1
        )
        pre_filter.cluster_threshold = _number(
            pf_node,
            "cluster-threshold",
            0.8,
            "pre-filter/cluster-threshold",
            minimum=0,
            maximum=1,
        )
        emb_path = pf_node.findtext("embeddings-path")
        if emb_path and emb_path.strip():
            pre_filter.embeddings_path = _resolve_path(config_path, emb_path.strip())
        queries_file = pf_node.findtext("queries-file")
        if queries_file and queries_file.strip():
            pre_filter.queries_file = _resolve_path(config_path, queries_file.strip())

    emb_node = root.find("embeddings")
    embeddings_config = EmbeddingsConfig()
    if emb_node is not None:
        embeddings_config.provider = _choice(
            emb_node,
            "provider",
            "fastembed",
            "embeddings/provider",
            {"fastembed", "openai"},
        )
        embeddings_config.model = _nonempty(
            emb_node, "model", "intfloat/multilingual-e5-large", "embeddings/model"
        )

    llm_config = _parse_llm(root)
    email_node = root.find("email")
    email = EmailConfig()
    if email_node is not None:
        email.to_addr = (email_node.findtext("to") or "").strip() or None
        email.from_addr = (email_node.findtext("from") or "").strip() or None
        email.subject = (email_node.findtext("subject") or "").strip() or None

    log_node = root.find("logging")
    logging_config = LoggingConfig()
    if log_node is not None:
        logging_config.level = _choice(
            log_node,
            "level",
            "INFO",
            "logging/level",
            {"debug", "info", "warning", "error", "critical"},
        ).upper()
        log_file = log_node.findtext("file")
        if log_file and log_file.strip():
            logging_config.file = _resolve_path(config_path, log_file.strip())

    db_node = root.find("database")
    db_config = DatabaseConfig()
    if db_node is not None:
        db_config.enabled = _boolean(db_node, "enabled", False, "database/enabled")
        db_config.connection_string = db_node.findtext("connection-string")

    http_node = root.find("http")
    http_config = HttpConfig()
    if http_node is not None:
        http_config = HttpConfig(
            connect_timeout=_number(
                http_node,
                "connect-timeout",
                5,
                "http/connect-timeout",
                minimum=0.000000001,
            ),
            read_timeout=_number(
                http_node, "read-timeout", 20, "http/read-timeout", minimum=0.000000001
            ),
            max_feed_bytes=_integer(
                http_node, "max-feed-bytes", 5_242_880, "http/max-feed-bytes", minimum=1
            ),
            max_article_bytes=_integer(
                http_node,
                "max-article-bytes",
                10_485_760,
                "http/max-article-bytes",
                minimum=1,
            ),
            retries=_integer(http_node, "retries", 2, "http/retries", minimum=0),
            backoff_seconds=_number(
                http_node, "backoff-seconds", 0.5, "http/backoff-seconds", minimum=0
            ),
            per_host_concurrency=_integer(
                http_node,
                "per-host-concurrency",
                2,
                "http/per-host-concurrency",
                minimum=1,
            ),
        )

    prompt_node = root.find("prompt")
    prompt = None
    if prompt_node is not None:
        prompt_file = prompt_node.attrib.get("file")
        if not prompt_file:
            raise ValueError("Prompt element must have a 'file' attribute.")
        full_prompt_path = _resolve_path(config_path, prompt_file)
        try:
            prompt = Path(full_prompt_path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise ValueError(f"Prompt file not found: {full_prompt_path}")
        if not prompt:
            raise _invalid("prompt", "empty", "a nonempty prompt file")
    if summary and not prompt:
        raise _invalid("prompt", "missing", "a prompt when summary is true")
    if (
        email.to_addr
        and not email.from_addr
        and not env_file
        and not os.environ.get("RESEND_FROM_EMAIL")
    ):
        raise _invalid("email/from", "missing", "a sender or RESEND_FROM_EMAIL")

    return AppConfig(
        feeds_file=feeds_file,
        env_file=env_file,
        limit=limit,
        max_age_hours=max_age_hours,
        summary=summary,
        pre_filter=pre_filter,
        embeddings=embeddings_config,
        llm=llm_config,
        email=email,
        logging=logging_config,
        database=db_config,
        http=http_config,
        prompt=prompt,
        max_article_length=max_len,
        extractor=extractor,
        concurrency=concurrency,
    )
