"""Configuration loading for RSS feeds and application settings."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .models import FeedConfig

logger = logging.getLogger(__name__)


@dataclass
class ClassificationConfig:
    enabled: bool = True
    model: str = "jev-latest"
    threshold: float = 0.50


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
class AppConfig:
    feeds_file: str
    env_file: Optional[str] = None
    limit: int = 10
    max_age_hours: Optional[float] = None
    summary: bool = False
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    prompt: Optional[str] = None
    max_article_length: int = 100
    concurrency: int = 10
    llm_model: Optional[str] = None


def parse_feeds_config(path: str) -> List[FeedConfig]:
    """Parse the OPML configuration file and return feed definitions.

    OPML (Outline Processor Markup Language) is maintained as the feed configuration format
    because it is the de facto universal standard for RSS feed subscriptions across feed readers
    (Feedly, NetNewsWire, Inoreader, etc.), allowing direct export and import.
    """
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


def load_dotenv(path: str | Path = ".env") -> Dict[str, str]:
    """Parse key-value pairs from a .env file and set in os.environ if not present."""
    env_path = Path(path)
    if not env_path.is_file():
        return {}

    loaded = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key not in os.environ:
                os.environ[key] = val
            loaded[key] = val
    return loaded


# Backward compatibility alias
parse_env_config = load_dotenv


def parse_app_config(path: str) -> AppConfig:
    """Parse the main application configuration from a TOML file."""
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    logger.info("Loading application configuration from %s", config_path)
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(
            f"Failed to parse TOML configuration from {path}: {exc}"
        ) from exc

    feeds_raw = data.get("feeds") or data.get("feeds_file")
    if not feeds_raw:
        raise ValueError("Configuration missing required 'feeds' (path to OPML file)")
    feeds_file = _resolve_path(config_path, str(feeds_raw).strip())

    env_file_raw = data.get("env_file") or data.get("env")
    env_file = (
        _resolve_path(config_path, str(env_file_raw).strip()) if env_file_raw else None
    )

    limit = int(data.get("limit", 10))
    max_age_val = data.get("max_age_hours")
    max_age_hours = float(max_age_val) if max_age_val is not None else None
    summary = bool(data.get("summary", False))
    max_article_length = int(data.get("max_article_length", 100))
    concurrency = int(data.get("concurrency", 10))

    # Classification (Jev)
    class_dict = data.get("classification") or {}
    classification_config = ClassificationConfig(
        enabled=bool(class_dict.get("enabled", True)),
        model=str(class_dict.get("model", "jev-latest")),
        threshold=float(class_dict.get("threshold", 0.50)),
    )

    # Email
    email_dict = data.get("email") or {}
    email = EmailConfig(
        to_addr=email_dict.get("to"),
        from_addr=email_dict.get("from"),
        subject=email_dict.get("subject"),
    )

    # Logging
    log_dict = data.get("logging") or {}
    log_file_raw = log_dict.get("file")
    logging_config = LoggingConfig(
        level=str(log_dict.get("level", "INFO")),
        file=_resolve_path(config_path, str(log_file_raw)) if log_file_raw else None,
    )

    # Prompt
    prompt = None
    prompt_file_raw = data.get("prompt_file")
    if not prompt_file_raw and isinstance(data.get("prompt"), dict):
        prompt_file_raw = data["prompt"].get("file")

    if prompt_file_raw:
        full_prompt_path = _resolve_path(config_path, str(prompt_file_raw))
        prompt_path_obj = Path(full_prompt_path)
        if not prompt_path_obj.exists():
            raise ValueError(f"Prompt file not found: {full_prompt_path}")
        prompt = prompt_path_obj.read_text(encoding="utf-8").strip()
    elif isinstance(data.get("prompt"), str):
        prompt = data["prompt"].strip()

    # LLM
    llm_dict = data.get("llm") or {}
    llm_model = llm_dict.get("model")

    return AppConfig(
        feeds_file=feeds_file,
        env_file=env_file,
        limit=limit,
        max_age_hours=max_age_hours,
        summary=summary,
        classification=classification_config,
        email=email,
        logging=logging_config,
        prompt=prompt,
        max_article_length=max_article_length,
        concurrency=concurrency,
        llm_model=llm_model,
    )
