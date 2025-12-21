"""Configuration loading for RSS feeds."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
from xml.etree import ElementTree as ET
import os

try:
    import boto3
except ImportError:
    boto3 = None

from .models import FeedConfig

logger = logging.getLogger(__name__)


@dataclass
class PreFilterConfig:
    enabled: bool = False
    embeddings_path: Optional[str] = None
    cluster_threshold: float = 0.8


@dataclass
class SecretsConfig:
    openai_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    resend_api_key: Optional[str] = None
    resend_from_email: Optional[str] = None


@dataclass
class EmailConfig:
    to_addr: Optional[str] = None
    subject: Optional[str] = None


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = None


@dataclass
class AppConfig:
    feeds_file: str
    env_file: Optional[str]
    limit: int = 10
    max_age_hours: Optional[float] = None
    summary: bool = False
    pre_filter: PreFilterConfig = field(default_factory=PreFilterConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    prompt: Optional[str] = None
    max_article_length: int = 5000


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


def _fetch_ssm_parameter(client: Any, parameter_name: str) -> Optional[str]:
    """Fetch a parameter from SSM, returning None if not found or on error."""
    try:
        response = client.get_parameter(Name=parameter_name, WithDecryption=True)
        return response.get("Parameter", {}).get("Value")
    except Exception as exc:
        logger.debug("Failed to fetch SSM parameter %s: %s", parameter_name, exc)
        return None


def load_secrets(
    env_file: Optional[str],
    use_ssm: bool = False,
    ssm_prefix: str = "/rss-morning",
) -> SecretsConfig:
    """
    Load secrets from multiple sources with strict conflict resolution.

    Sources checked:
    1. SSM (if use_ssm=True and boto3 available)
    2. os.environ
    3. env_file (XML)

    Conflict Policy:
    If a secret is defined in more than one source, raising ValueError.
    """
    # Mapping of internal secret name -> (env_var_name, ssm_suffix)
    secret_map = {
        "openai_api_key": ("OPENAI_API_KEY", "OPENAI_API_KEY"),
        "google_api_key": ("GOOGLE_API_KEY", "GOOGLE_API_KEY"),
        "resend_api_key": ("RESEND_API_KEY", "RESEND_API_KEY"),
        "resend_from_email": ("RESEND_FROM_EMAIL", "RESEND_FROM_EMAIL"),
    }

    # 1. Load from XML
    xml_secrets = {}
    if env_file:
        xml_data = parse_env_config(env_file)
        for secret_field, (env_var_name, _) in secret_map.items():
            if env_var_name in xml_data:
                xml_secrets[secret_field] = xml_data[env_var_name]

    # 2. Load from os.environ
    env_secrets = {}
    for secret_field, (env_var_name, _) in secret_map.items():
        val = os.environ.get(env_var_name)
        if val:
            env_secrets[secret_field] = val

    # 3. Load from SSM
    ssm_secrets = {}
    if use_ssm:
        if boto3 is None:
            logger.warning("SSM requested but boto3 is not installed; skipping SSM.")
        else:
            try:
                ssm = boto3.client("ssm")
                for secret_field, (_, ssm_suffix) in secret_map.items():
                    param_name = f"{ssm_prefix}/{ssm_suffix}"
                    val = _fetch_ssm_parameter(ssm, param_name)
                    if val:
                        ssm_secrets[secret_field] = val
            except Exception as exc:
                logger.error("Error initializing SSM client: %s", exc)

    # 4. Conflict Resolution & Merging
    final_secrets = {}
    for secret_field in secret_map:
        sources_found = []
        val_ssm = ssm_secrets.get(secret_field)
        val_env = env_secrets.get(secret_field)
        val_xml = xml_secrets.get(secret_field)

        if val_ssm is not None:
            sources_found.append("SSM")
        if val_env is not None:
            sources_found.append("Environment")
        if val_xml is not None:
            sources_found.append("XML")

        if len(sources_found) > 1:
            raise ValueError(
                f"Secret conflict for '{secret_field}': found in {', '.join(sources_found)}. "
                "Strict conflict resolution enabled; please define in only one source."
            )

        if val_ssm:
            final_secrets[secret_field] = val_ssm
        elif val_env:
            final_secrets[secret_field] = val_env
        elif val_xml:
            final_secrets[secret_field] = val_xml

    # 5. Validation: Ensure all secrets are present
    missing = [k for k in secret_map if k not in final_secrets]
    if missing:
        raise ValueError(
            f"Missing required secrets: {', '.join(missing)}. "
            "All secrets must be provided via XML, Environment, or SSM."
        )

    return SecretsConfig(**final_secrets)


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

    # Secrets loading
    # Note: caller or args might override use_ssm, but for now we defaults to False or check env?
    # We can perhaps check if we are in AWS or have a flag?
    # The user instruction implies we might not have a CLI flag for SSM yet, but we should support it.
    # Let's assume we want to support SSM if configured. For now, rely on default of False,
    # OR we can update `parse_app_config` signature to accept `use_ssm`.
    # But `parse_app_config` is called early.
    # Let's check an Environment Variable RSS_MORNING_USE_SSM to toggle this for now?
    # Or strict logic: if `env_file` is NOT present, maybe we check SSM?
    # Actually, let's look at the `AppConfig` signature change.

    use_ssm = os.environ.get("RSS_MORNING_USE_SSM", "false").lower() == "true"
    secrets = load_secrets(env_file=env_file, use_ssm=use_ssm)

    # Simple values
    limit = int(root.findtext("limit", "10"))

    max_age_node = root.find("max-age-hours")
    max_age_hours = (
        float(max_age_node.text)
        if max_age_node is not None and max_age_node.text
        else None
    )

    summary = root.findtext("summary", "false").lower() == "true"
    max_len = int(root.findtext("max-article-length", "5000"))

    # Pre-filter
    pf_node = root.find("pre-filter")
    pre_filter = PreFilterConfig()
    if pf_node is not None:
        pre_filter.enabled = pf_node.findtext("enabled", "false").lower() == "true"
        emb_path = pf_node.findtext("embeddings-path")
        if emb_path:
            pre_filter.embeddings_path = _resolve_path(config_path, emb_path)

        ct_node = pf_node.find("cluster-threshold")
        if ct_node is not None and ct_node.text:
            pre_filter.cluster_threshold = float(ct_node.text)

    # Email
    email_node = root.find("email")
    email = EmailConfig()
    if email_node is not None:
        email.to_addr = email_node.findtext("to")
        # from_addr is now a secret/env var usually, but kept in config if needed?
        # The user instructions said RESEND_FROM_EMAIL is a secret.
        # We removed from_addr from EmailConfig to move it to SecretsConfig.
        email.subject = email_node.findtext("subject")

    # Logging
    log_node = root.find("logging")
    logging_config = LoggingConfig()
    if log_node is not None:
        logging_config.level = log_node.findtext("level", "INFO")
        log_file = log_node.findtext("file")
        if log_file:
            logging_config.file = _resolve_path(config_path, log_file)

    # Prompt
    prompt_node = root.find("prompt")
    prompt = (
        prompt_node.text.strip()
        if prompt_node is not None and prompt_node.text
        else None
    )

    return AppConfig(
        feeds_file=feeds_file,
        env_file=env_file,
        limit=limit,
        max_age_hours=max_age_hours,
        summary=summary,
        pre_filter=pre_filter,
        email=email,
        secrets=secrets,
        logging=logging_config,
        prompt=prompt,
        max_article_length=max_len,
    )
