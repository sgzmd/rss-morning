"""Configuration loading for RSS feeds and application settings."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .models import AreaConfig, FeedConfig, TechnologyFootprint
from .tenant import (
    get_tenants_base_dir,
    validate_tenant_id,
    validate_tenant_path_isolation,
)

logger = logging.getLogger(__name__)

KNOWN_CONFIG_KEYS = {
    "feeds",
    "feeds_file",
    "limit",
    "max_age_hours",
    "summary",
    "concurrency",
    "max_article_length",
    "prompt_file",
    "prompt",
    "grounding_rules_file",
    "grounding_rules",
    "holdings_file",
    "holdings",
    "env_file",
    "env",
    "classification",
    "technologies",
    "email",
    "logging",
    "llm",
    "digest",
    "areas",
    "extractor",
    "tenant_id",
    "profile",
    "title",
    "display_title",
    "subtitle",
    "timezone",
}

DEFAULT_AREAS: Dict[str, AreaConfig] = {
    "mobile_security": AreaConfig(
        key="mobile_security",
        label="Mobile Security",
        description=(
            "Mobile application security, Android and iOS vulnerabilities, "
            "mobile malware, device attestation, and client integrity."
        ),
        threshold=0.40,
        technologies=(
            "Android (AOSP, Keystore, Play Integrity)",
            "iOS (Secure Enclave, App Attest, sandbox)",
            "Mobile application runtime security & malware",
        ),
        priority="high",
    ),
    "security_ux": AreaConfig(
        key="security_ux",
        label="Security UX & Modern Auth",
        description=(
            "How user experience influences security, how users interact with security systems, "
            "passkeys, FIDO2, WebAuthn, modern authentication methods, biometric login, "
            "MFA friction and adoption, and end-user identity protection."
        ),
        threshold=0.40,
        technologies=(
            "Passkeys (FIDO2/WebAuthn)",
            "Consumer biometric authentication",
            "MFA adoption & authentication UX",
        ),
        priority="high",
    ),
    "corporate_security": AreaConfig(
        key="corporate_security",
        label="Corporate Security & Internal Threat",
        description=(
            "Enterprise networking, VPN, SD-WAN, firewalls, identity providers, "
            "managed devices, browsers, workplace IT infrastructure, and insider threats."
        ),
        threshold=0.45,
        priority="normal",
    ),
    "account_takeover": AreaConfig(
        key="account_takeover",
        label="Account Takeover",
        description=(
            "Account takeover, credential stuffing, session hijacking, "
            "password spraying, brute force, and customer identity compromise."
        ),
        threshold=0.40,
        technologies=(
            "Credential stuffing defenses",
            "Session hijacking & token theft",
            "Consumer account protection",
        ),
        priority="high",
    ),
    "end_user_security": AreaConfig(
        key="end_user_security",
        label="End-User Security (Passkeys & Modern Auth)",
        description=(
            "Passkeys, FIDO2, WebAuthn, modern authentication methods, "
            "biometric login, MFA adoption, and end-user identity protection."
        ),
        threshold=0.40,
        technologies=(
            "Passkeys (FIDO2/WebAuthn)",
            "Consumer biometric authenticators",
            "FIDO Alliance standards",
        ),
        priority="high",
    ),
    "ai_security": AreaConfig(
        key="ai_security",
        label="AI Security",
        description=(
            "AI model vulnerabilities, prompt injection, AI agent safety, "
            "and security implications of LLMs and generative AI."
        ),
        threshold=0.50,
        priority="normal",
    ),
    "other": AreaConfig(
        key="other",
        label="Everything Else & Notable CVEs",
        description=(
            "General security news, notable CVEs, software vulnerabilities, "
            "infrastructure, or material not fitting any specific focus area above."
        ),
        threshold=0.75,
        priority="low",
    ),
}


@dataclass
class ClassificationConfig:
    enabled: bool = True
    model: str = "jev-latest"
    threshold: float = 0.50
    relevance_instructions: Optional[str] = None
    relevance_criteria_true: Optional[str] = None
    relevance_criteria_false: Optional[str] = None


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
class DigestConfig:
    """Settings controlling the editorial digest."""

    exec_summary_points: int = 5
    max_topics: int = 6
    editor_article_chars: int = 1500


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
    digest: DigestConfig = field(default_factory=DigestConfig)
    prompt: Optional[str] = None
    max_article_length: int = 100
    concurrency: int = 10
    llm_model: Optional[str] = None
    reasoning_effort: Optional[str] = "low"
    areas: Dict[str, AreaConfig] = field(default_factory=lambda: dict(DEFAULT_AREAS))
    technologies: Optional[TechnologyFootprint] = None
    tenant_id: Optional[str] = None
    profile: str = "security"
    title: Optional[str] = None
    subtitle: Optional[str] = None
    timezone: Optional[str] = None
    extractor: str = "trafilatura"
    grounding_rules: Optional[str] = None
    grounding_rules_file: Optional[str] = None
    holdings_file: Optional[str] = None


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


def _resolve_path(
    base_path: Path,
    target_path: str,
    tenant_dir: Optional[Path] = None,
    tenants_base_dir: Optional[Path | str] = None,
) -> str:
    """Resolve a path relative to the base config file if it's not absolute."""
    target = Path(target_path)
    if target.is_absolute():
        resolved = target.resolve()
    else:
        resolved = (base_path.parent / target).resolve()

    if tenant_dir is not None:
        base = (
            tenants_base_dir if tenants_base_dir is not None else get_tenants_base_dir()
        )
        validate_tenant_path_isolation(resolved, tenant_dir, tenants_base_dir=base)

    return str(resolved)


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


def parse_app_config(path: str | Path, tenant_id: Optional[str] = None) -> AppConfig:
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

    # Check for unknown settings
    for key in data.keys():
        if key not in KNOWN_CONFIG_KEYS:
            raise ValueError(
                f"Unknown configuration setting '{key}' in {path}. "
                f"Valid settings are: {', '.join(sorted(KNOWN_CONFIG_KEYS))}"
            )

    # Validate extractor setting explicitly
    raw_extractor = data.get("extractor")
    if raw_extractor is not None:
        extractor = str(raw_extractor).strip().lower()
        if extractor != "trafilatura":
            raise ValueError(
                f"Unsupported extractor '{extractor}'. 'trafilatura' is currently the only supported article extractor. "
                "Please set extractor = 'trafilatura' or remove the setting."
            )
    else:
        extractor = "trafilatura"

    # Inferred tenant directory and path isolation
    inferred_tenant_id = tenant_id or data.get("tenant_id")
    if inferred_tenant_id:
        inferred_tenant_id = validate_tenant_id(str(inferred_tenant_id))

    tenant_dir: Optional[Path] = None
    try:
        tenants_base = get_tenants_base_dir()
        config_path.relative_to(tenants_base)
        tenant_dir = config_path.parent
        if not inferred_tenant_id:
            inferred_tenant_id = validate_tenant_id(tenant_dir.name)
    except ValueError:
        if inferred_tenant_id:
            tenant_dir = config_path.parent

    feeds_raw = data.get("feeds") or data.get("feeds_file")
    if not feeds_raw:
        raise ValueError("Configuration missing required 'feeds' (path to OPML file)")
    feeds_file = _resolve_path(
        config_path,
        str(feeds_raw).strip(),
        tenant_dir=tenant_dir,
        tenants_base_dir=tenants_base,
    )

    env_file_raw = data.get("env_file") or data.get("env")
    env_file = (
        _resolve_path(
            config_path,
            str(env_file_raw).strip(),
            tenant_dir=tenant_dir,
            tenants_base_dir=tenants_base,
        )
        if env_file_raw
        else None
    )

    limit = int(data.get("limit", 10))
    max_age_val = data.get("max_age_hours")
    max_age_hours = float(max_age_val) if max_age_val is not None else None
    summary = bool(data.get("summary", False))
    concurrency = int(data.get("concurrency", 10))

    # Classification (Jev)
    class_dict = data.get("classification") or {}
    rel_dict = class_dict.get("relevance") or {}
    rel_instructions = class_dict.get("relevance_instructions") or rel_dict.get(
        "instructions"
    )
    rel_true = (
        class_dict.get("relevance_criteria_true")
        or rel_dict.get("criteria_true")
        or rel_dict.get("true")
    )
    rel_false = (
        class_dict.get("relevance_criteria_false")
        or rel_dict.get("criteria_false")
        or rel_dict.get("false")
    )
    classification_config = ClassificationConfig(
        enabled=bool(class_dict.get("enabled", True)),
        model=str(class_dict.get("model", "jev-latest")),
        threshold=float(class_dict.get("threshold", 0.50)),
        relevance_instructions=str(rel_instructions).strip()
        if rel_instructions
        else None,
        relevance_criteria_true=str(rel_true).strip() if rel_true else None,
        relevance_criteria_false=str(rel_false).strip() if rel_false else None,
    )

    # Technologies footprint carveout
    tech_dict = data.get("technologies") or {}
    tech_footprint: Optional[TechnologyFootprint] = None
    if tech_dict:
        tech_desc = str(tech_dict.get("description", "")).strip()
        raw_tech_items = (
            tech_dict.get("prioritized")
            or tech_dict.get("stack")
            or tech_dict.get("technologies")
            or []
        )
        tech_items = (
            tuple(str(t) for t in raw_tech_items)
            if isinstance(raw_tech_items, (list, tuple))
            else ()
        )
        tech_footprint = TechnologyFootprint(
            description=tech_desc, technologies=tech_items
        )

    # Areas
    areas_raw = data.get("areas") or class_dict.get("areas")
    if areas_raw and isinstance(areas_raw, dict):
        parsed_areas: Dict[str, AreaConfig] = {}
        for k, v in areas_raw.items():
            if isinstance(v, dict):
                label = str(v.get("label") or k.replace("_", " ").title())
                desc = str(v.get("description") or label)
                th = float(v.get("threshold", classification_config.threshold))
                raw_tech = v.get("technologies") or ()
                techs = (
                    tuple(str(t) for t in raw_tech)
                    if isinstance(raw_tech, (list, tuple))
                    else ()
                )
                prio = str(v.get("priority", "normal")).lower()
                parsed_areas[k] = AreaConfig(
                    key=k,
                    label=label,
                    description=desc,
                    threshold=th,
                    technologies=techs,
                    priority=prio,
                )
        if "other" not in parsed_areas:
            parsed_areas["other"] = DEFAULT_AREAS["other"]
        areas = parsed_areas
    else:
        areas = dict(DEFAULT_AREAS)

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
    if log_file_raw and str(log_file_raw).strip().lower() in ("none", "", "null"):
        log_file_raw = None
    logging_config = LoggingConfig(
        level=str(log_dict.get("level", "INFO")),
        file=_resolve_path(
            config_path,
            str(log_file_raw),
            tenant_dir=tenant_dir,
            tenants_base_dir=tenants_base,
        )
        if log_file_raw
        else None,
    )

    # Prompt
    prompt = None
    prompt_file_raw = data.get("prompt_file")
    if not prompt_file_raw and isinstance(data.get("prompt"), dict):
        prompt_file_raw = data["prompt"].get("file")

    if prompt_file_raw:
        full_prompt_path = _resolve_path(
            config_path,
            str(prompt_file_raw),
            tenant_dir=tenant_dir,
            tenants_base_dir=tenants_base,
        )
        prompt_path_obj = Path(full_prompt_path)
        if not prompt_path_obj.exists():
            raise ValueError(f"Prompt file not found: {full_prompt_path}")
        prompt = prompt_path_obj.read_text(encoding="utf-8").strip()
    elif isinstance(data.get("prompt"), str):
        prompt = data["prompt"].strip()

    # Grounding Rules
    grounding_rules = None
    grounding_rules_file_raw = data.get("grounding_rules_file")
    if grounding_rules_file_raw:
        full_gr_path = _resolve_path(
            config_path,
            str(grounding_rules_file_raw),
            tenant_dir=tenant_dir,
            tenants_base_dir=tenants_base,
        )
        gr_path_obj = Path(full_gr_path)
        if not gr_path_obj.exists():
            raise ValueError(f"Grounding rules file not found: {full_gr_path}")
        grounding_rules = gr_path_obj.read_text(encoding="utf-8").strip()
    elif isinstance(data.get("grounding_rules"), str):
        grounding_rules = data["grounding_rules"].strip()

    # Holdings file (optional structured context)
    holdings_file_raw = data.get("holdings_file")
    holdings_file = (
        _resolve_path(
            config_path,
            str(holdings_file_raw),
            tenant_dir=tenant_dir,
            tenants_base_dir=tenants_base,
        )
        if holdings_file_raw
        else None
    )

    # Tenant Profile, Title, Subtitle, Timezone
    profile = str(data.get("profile", "security")).strip().lower()
    title = data.get("title") or data.get("display_title")
    subtitle = data.get("subtitle")
    timezone_name = data.get("timezone")

    # LLM
    llm_dict = data.get("llm") or {}
    llm_model = llm_dict.get("model")
    reasoning_effort = llm_dict.get("reasoning_effort", "low")
    if reasoning_effort and str(reasoning_effort).strip().lower() in (
        "none",
        "false",
        "off",
        "null",
    ):
        reasoning_effort = None

    # Digest (Pyramid settings)
    digest_dict = data.get("digest") or {}
    # Legacy fallback: max_article_length in root can act as editor_article_chars
    editor_chars = int(
        digest_dict.get("editor_article_chars", data.get("max_article_length", 1500))
    )
    digest_config = DigestConfig(
        exec_summary_points=int(digest_dict.get("exec_summary_points", 5)),
        max_topics=int(digest_dict.get("max_topics", 6)),
        editor_article_chars=editor_chars,
    )

    return AppConfig(
        feeds_file=feeds_file,
        env_file=env_file,
        limit=limit,
        max_age_hours=max_age_hours,
        summary=summary,
        classification=classification_config,
        email=email,
        logging=logging_config,
        digest=digest_config,
        prompt=prompt,
        max_article_length=editor_chars,
        concurrency=concurrency,
        llm_model=llm_model,
        reasoning_effort=reasoning_effort,
        areas=areas,
        technologies=tech_footprint,
        tenant_id=inferred_tenant_id,
        profile=profile,
        title=title,
        subtitle=subtitle,
        timezone=timezone_name,
        extractor=extractor,
        grounding_rules=grounding_rules,
        grounding_rules_file=holdings_file_raw,  # backward reference if needed
        holdings_file=holdings_file,
    )
