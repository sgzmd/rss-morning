"""Configuration loading for RSS feeds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .models import FeedConfig

logger = logging.getLogger(__name__)


@dataclass
class PreFilterConfig:
    enabled: bool = False
    embeddings_path: Optional[str] = None
    cluster_threshold: float = 0.8
    queries_file: Optional[str] = None


@dataclass
class EmbeddingsConfig:
    provider: str = "fastembed"
    model: str = "intfloat/multilingual-e5-large"


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
class AppConfig:
    feeds_file: str
    env_file: Optional[str]
    limit: int = 10
    max_age_hours: Optional[float] = None
    summary: bool = False
    pre_filter: PreFilterConfig = field(default_factory=PreFilterConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
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
    env_vars = {}
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


def parse_app_config(path: str) -> AppConfig:
    """Parse the main application configuration XML."""
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    logger.info("Loading application configuration from %s", config_path)
    tree = ET.parse(config_path)
    root = tree.getroot()

    # Feeds
    feeds_node = root.find("feeds")
    if feeds_node is None or not feeds_node.text:
        raise ValueError("Config missing <feeds> path")
    feeds_file = _resolve_path(config_path, feeds_node.text.strip())

    # Env
    env_node = root.find("env")
    env_file = (
        _resolve_path(config_path, env_node.text.strip())
        if env_node is not None and env_node.text
        else None
    )

    # Simple values
    limit = int(root.findtext("limit", "10"))

    max_age_node = root.find("max-age-hours")
    max_age_hours = (
        float(max_age_node.text)
        if max_age_node is not None and max_age_node.text
        else None
    )

    summary = root.findtext("summary", "false").lower() == "true"
    max_len = int(root.findtext("max-article-length", "100"))

    # Pre-filter
    pf_node = root.find("pre-filter")
    pre_filter = PreFilterConfig()
    if pf_node is not None:
        pre_filter.enabled = pf_node.findtext("enabled", "false").lower() == "true"
        emb_path = pf_node.findtext("embeddings-path")
        if emb_path:
            pre_filter.embeddings_path = _resolve_path(config_path, emb_path)

        queries_file = pf_node.findtext("queries-file")
        if queries_file:
            pre_filter.queries_file = _resolve_path(config_path, queries_file)

        ct_node = pf_node.find("cluster-threshold")
        if ct_node is not None and ct_node.text:
            pre_filter.cluster_threshold = float(ct_node.text)

    # Embeddings
    emb_node = root.find("embeddings")
    embeddings_config = EmbeddingsConfig()
    if emb_node is not None:
        embeddings_config.provider = emb_node.findtext("provider", "fastembed")
        embeddings_config.model = emb_node.findtext(
            "model", "intfloat/multilingual-e5-large"
        )

    # Email
    email_node = root.find("email")
    email = EmailConfig()
    if email_node is not None:
        email.to_addr = email_node.findtext("to")
        email.from_addr = email_node.findtext("from")
        email.subject = email_node.findtext("subject")

    # Logging
    log_node = root.find("logging")
    logging_config = LoggingConfig()
    if log_node is not None:
        logging_config.level = log_node.findtext("level", "INFO")
        log_file = log_node.findtext("file")
        if log_file:
            logging_config.file = _resolve_path(config_path, log_file)

    # Database
    db_node = root.find("database")
    db_config = DatabaseConfig()
    if db_node is not None:
        db_config.enabled = db_node.findtext("enabled", "false").lower() == "true"
        db_config.connection_string = db_node.findtext("connection-string")

    # Prompt
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

    extractor = root.findtext("extractor", "newspaper")
    concurrency = int(root.findtext("concurrency", "10"))

    return AppConfig(
        feeds_file=feeds_file,
        env_file=env_file,
        limit=limit,
        max_age_hours=max_age_hours,
        summary=summary,
        pre_filter=pre_filter,
        embeddings=embeddings_config,
        email=email,
        logging=logging_config,
        database=db_config,
        prompt=prompt,
        max_article_length=max_len,
        extractor=extractor,
        concurrency=concurrency,
    )
