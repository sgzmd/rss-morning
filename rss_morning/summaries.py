"""Resilient LLM integrations for structured article summaries."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from bs4 import BeautifulSoup
from openai import OpenAI

from .metrics import RunStats

try:
    genai: Any = importlib.import_module("google.genai")
    types: Any = importlib.import_module("google.genai.types")
except Exception:  # pragma: no cover - optional dependency
    genai = None
    types = None

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "bytedance-seed/seed-2.0-mini"
DEFAULT_OPENROUTER_FALLBACKS = (
    "z-ai/glm-4.7-flash",
    "openai/gpt-4o-mini",
)
SUMMARY_SCHEMA_VERSION = "4"
REQUIRED_SUMMARY_FIELDS = (
    "title",
    "rank-reasoning",
    "what",
    "so-what",
    "now-what",
)

RELEVANCE_DECISION_INSTRUCTION = """For every submitted article, make an explicit relevance decision for the target audience described above.

Return exactly one item for every submitted URL. Set `relevant` to true only when the article is directly useful or actionable for that audience; a pre-filter match or input category is only a candidate signal and must not override your judgment. When `relevant` is false, return an empty category and empty strings for every summary field. When it is true, provide the complete category and summary. Do not invent a security connection for an otherwise irrelevant article."""

SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summaries"],
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url", "relevant", "category", "summary"],
                "properties": {
                    "url": {"type": "string"},
                    "relevant": {"type": "boolean"},
                    "category": {"type": "string"},
                    "summary": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(REQUIRED_SUMMARY_FIELDS),
                        "properties": {
                            field: {"type": "string"}
                            for field in REQUIRED_SUMMARY_FIELDS
                        },
                    },
                },
            },
        },
    },
}

EXECUTIVE_SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["executive-summary"],
    "properties": {
        "executive-summary": {"type": "string", "minLength": 1},
    },
}

EXECUTIVE_SUMMARY_PROMPT = """Write one coherent executive briefing that synthesizes the complete news update below.

Use two to four short paragraphs. Connect the strongest shared themes, explain their overall implications, and identify the highest priorities. Do not write a list, recap every story one by one, add a heading, or mention article processing, filtering, or completeness. Do not invent facts. Return only the required structured JSON."""


@dataclass(frozen=True)
class SummarySettings:
    """All configuration that can affect a summary response or its cost."""

    provider: str = "openrouter"
    model: str = DEFAULT_OPENROUTER_MODEL
    fallback_models: tuple[str, ...] = DEFAULT_OPENROUTER_FALLBACKS
    routing: str = "price"
    max_input_price_per_million: float = 0.20
    max_output_price_per_million: float = 0.75
    require_structured_output: bool = True
    max_batch_articles: int = 20
    max_input_tokens: int = 30_000
    request_timeout_seconds: float = 90
    max_attempts: int = 3
    max_split_depth: int = 6
    cache_enabled: bool = True


@dataclass(frozen=True)
class ProviderResponse:
    """Provider-neutral response and private billing metrics."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderError(RuntimeError):
    """HTTP-shaped provider error used by adapters and deterministic tests."""

    def __init__(
        self,
        status_code: int,
        message: str = "provider error",
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class ResponseValidationError(ValueError):
    """A stochastic provider response that failed local reconciliation."""


def sanitize_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text()


def build_summary_input(articles: list[dict]) -> str:
    """Prepare the LLM request payload from article data."""
    prepared = [
        {
            "id": f"article-{index}",
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "summary": article.get("summary", ""),
            "content": article.get("text", "") or "",
            "category": article.get("category", ""),
        }
        for index, article in enumerate(articles, start=1)
    ]
    logger.debug("Prepared %d articles for summarisation", len(prepared))
    return json.dumps(prepared, ensure_ascii=False, separators=(",", ":"))


def build_executive_summary_input(summaries: list[dict]) -> str:
    """Prepare a summary-only synthesis input from validated results."""
    prepared = [
        {
            "category": item.get("category", ""),
            **{
                field: item.get("summary", {}).get(field, "")
                for field in REQUIRED_SUMMARY_FIELDS
            },
        }
        for item in summaries
    ]
    return json.dumps(prepared, ensure_ascii=False, separators=(",", ":"))


def _estimated_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def estimate_input_tokens(system_prompt: str, articles: list[dict]) -> int:
    """Return the deterministic conservative token estimate used by planning."""
    return _estimated_tokens(
        f"{system_prompt}\n\n{RELEVANCE_DECISION_INSTRUCTION}\n\n"
        f"{build_summary_input(articles)}"
    )


def _truncate_oversized(article: dict, prompt: str, budget: int) -> dict:
    bounded = dict(article)
    content = str(bounded.get("text") or "")
    if estimate_input_tokens(prompt, [bounded]) <= budget:
        return bounded
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        bounded["text"] = content[:middle]
        if estimate_input_tokens(prompt, [bounded]) <= budget:
            low = middle
        else:
            high = middle - 1
    bounded["text"] = content[:low]
    return bounded


def plan_batches(
    articles: list[dict],
    system_prompt: str,
    *,
    max_articles: int,
    max_input_tokens: int,
) -> list[list[dict]]:
    """Build deterministic sequential batches bounded by count and input estimate."""
    if max_articles <= 0 or max_input_tokens <= 0:
        raise ValueError("LLM batch limits must be positive")
    batches: list[list[dict]] = []
    current: list[dict] = []
    for source in articles:
        article = dict(source)
        candidate = [*current, article]
        if current and (
            len(candidate) > max_articles
            or estimate_input_tokens(system_prompt, candidate) > max_input_tokens
        ):
            batches.append(current)
            current = []
        if not current:
            article = _truncate_oversized(article, system_prompt, max_input_tokens)
        current.append(article)
    if current:
        batches.append(current)
    return batches


def models_within_caps(catalog: dict, settings: SummarySettings) -> list[str]:
    """Validate a recorded catalog fixture against configured price caps."""
    allowed = []
    configured = {settings.model, *settings.fallback_models}
    for model in catalog.get("models", []):
        if model.get("id") not in configured:
            continue
        prompt = float(model["prompt_price"]) * 1_000_000
        completion = float(model["completion_price"]) * 1_000_000
        if (
            prompt <= settings.max_input_price_per_million
            and completion <= settings.max_output_price_per_million
            and model.get("supports_structured_outputs")
        ):
            allowed.append(model["id"])
    return allowed


def cache_identity(
    settings: SummarySettings, system_prompt: str, batch: list[dict]
) -> str:
    """Hash every semantic input to one exact batch response."""
    semantic_settings = {
        "provider": settings.provider,
        "model_chain": [settings.model, *settings.fallback_models],
        "routing": settings.routing,
        "max_input_price_per_million": settings.max_input_price_per_million,
        "max_output_price_per_million": settings.max_output_price_per_million,
        "require_structured_output": settings.require_structured_output,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "prompt_hash": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "batch": batch,
    }
    encoded = json.dumps(
        semantic_settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def executive_cache_identity(settings: SummarySettings, summaries: list[dict]) -> str:
    """Hash every semantic input to one exact executive synthesis response."""
    semantic_settings = {
        "kind": "executive-summary",
        "provider": settings.provider,
        "model_chain": [settings.model, *settings.fallback_models],
        "routing": settings.routing,
        "max_input_price_per_million": settings.max_input_price_per_million,
        "max_output_price_per_million": settings.max_output_price_per_million,
        "require_structured_output": settings.require_structured_output,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "prompt_hash": hashlib.sha256(EXECUTIVE_SUMMARY_PROMPT.encode()).hexdigest(),
        "summaries": summaries,
    }
    encoded = json.dumps(
        semantic_settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def reconcile_response(parsed: dict, batch: list[dict]) -> dict:
    """Validate exact identities and restore trusted source data and order."""
    if not isinstance(parsed, dict):
        raise ResponseValidationError("LLM response must be an object")
    raw_summaries = parsed.get("summaries")
    if not isinstance(raw_summaries, list):
        raise ResponseValidationError("LLM summaries must be a list")
    raw_urls = [article.get("url") for article in batch]
    if any(not isinstance(url, str) or not url for url in raw_urls):
        raise ResponseValidationError("Submitted articles require unique nonempty URLs")
    source_by_url: dict[str, dict] = {str(article["url"]): article for article in batch}
    if len(source_by_url) != len(batch):
        raise ResponseValidationError("Submitted articles require unique nonempty URLs")
    decisions: dict[str, dict | None] = {}
    for item in raw_summaries:
        if not isinstance(item, dict) or not isinstance(item.get("summary"), dict):
            raise ResponseValidationError("LLM summary item must be an object")
        url = item.get("url")
        if not isinstance(url, str):
            raise ResponseValidationError("LLM returned an unknown URL")
        if url not in source_by_url:
            raise ResponseValidationError("LLM returned an unknown URL")
        if url in decisions:
            raise ResponseValidationError("LLM returned a duplicate URL")
        relevant = item.get("relevant")
        if not isinstance(relevant, bool):
            raise ResponseValidationError(
                "LLM relevance decision must be true or false"
            )
        if not isinstance(item.get("category"), str):
            raise ResponseValidationError("LLM category must be a string")
        raw_fields = item["summary"]
        if not relevant:
            if any(
                not isinstance(raw_fields.get(field), str)
                for field in REQUIRED_SUMMARY_FIELDS
            ):
                raise ResponseValidationError(
                    "LLM irrelevant decision has invalid summary fields"
                )
            decisions[url] = None
            continue
        clean_fields = {}
        for field in REQUIRED_SUMMARY_FIELDS:
            value = raw_fields.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ResponseValidationError(
                    f"LLM summary is missing required field {field}"
                )
            clean_fields[field] = sanitize_html(value)
        decisions[url] = {
            "url": url,
            "category": str(source_by_url[url].get("category") or ""),
            "summary": clean_fields,
        }
    missing = [url for url in source_by_url if url not in decisions]
    if missing:
        identifiers = [
            hashlib.sha256(str(url).encode()).hexdigest()[:12] for url in missing
        ]
        logger.warning(
            "LLM response omitted %d article(s): %s", len(missing), identifiers
        )
        raise ResponseValidationError("LLM response omitted submitted URLs")
    retained = []
    for article in batch:
        decision = decisions[str(article["url"])]
        if decision is not None:
            retained.append(decision)
    return {"summaries": retained}


def reconcile_executive_response(parsed: dict) -> str:
    """Validate and sanitize one coherent executive summary."""
    if not isinstance(parsed, dict):
        raise ResponseValidationError("Executive summary response must be an object")
    value = parsed.get("executive-summary")
    if not isinstance(value, str) or not value.strip():
        raise ResponseValidationError("Executive summary must be a nonempty string")
    return sanitize_html(value).strip()


def maximum_provider_attempts(
    article_count: int, max_attempts: int, max_split_depth: int
) -> int:
    """Return the finite worst-case calls for one initial batch."""
    if article_count <= 0 or max_attempts <= 0 or max_split_depth < 0:
        return 0
    levels = min(
        max_split_depth, math.ceil(math.log2(article_count)) if article_count > 1 else 0
    )
    nodes = sum(2**level for level in range(levels + 1))
    return nodes * max_attempts


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, ResponseValidationError)):
        return True
    status = getattr(exc, "status_code", None)
    return status == 429 or isinstance(status, int) and status >= 500


def _retry_delay(exc: Exception, attempt: int, jitter: Callable[[], float]) -> float:
    explicit = getattr(exc, "retry_after", None)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    if explicit is None and hasattr(headers, "get"):
        explicit = headers.get("retry-after") or headers.get("Retry-After")
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            pass
    return float(2**attempt) * (1.0 + 0.25 * jitter())


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return 0


def _gemini_schema(*, executive: bool = False) -> Any:
    if executive:
        return types.Schema(
            type=types.Type.OBJECT,
            required=["executive-summary"],
            properties={
                "executive-summary": types.Schema(type=types.Type.STRING),
            },
        )
    return types.Schema(
        type=types.Type.OBJECT,
        required=["summaries"],
        properties={
            "summaries": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["url", "relevant", "category", "summary"],
                    properties={
                        "url": types.Schema(type=types.Type.STRING),
                        "relevant": types.Schema(type=types.Type.BOOLEAN),
                        "category": types.Schema(type=types.Type.STRING),
                        "summary": types.Schema(
                            type=types.Type.OBJECT,
                            required=list(REQUIRED_SUMMARY_FIELDS),
                            properties={
                                field: types.Schema(type=types.Type.STRING)
                                for field in REQUIRED_SUMMARY_FIELDS
                            },
                        ),
                    },
                ),
            ),
        },
    )


def _make_provider_request(
    settings: SummarySettings, *, dry_run: bool, executive: bool = False
) -> Callable[[list[dict], str], ProviderResponse]:
    if settings.provider == "gemini":
        if genai is None or types is None:
            raise RuntimeError(
                "google-genai package is required for Gemini summaries but is not installed."
            )
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key and not dry_run:
            raise RuntimeError(
                "GOOGLE_API_KEY or GEMINI_API_KEY is required for Gemini summaries."
            )
        client = genai.Client(api_key=key)

        def gemini_request(_batch: list[dict], input_text: str) -> ProviderResponse:
            contents = [
                types.Content(
                    role="user", parts=[types.Part.from_text(text=input_text)]
                )
            ]
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_gemini_schema(executive=executive),
                http_options=types.HttpOptions(
                    timeout=int(settings.request_timeout_seconds * 1000)
                ),
            )
            text = "".join(
                chunk.text or ""
                for chunk in client.models.generate_content_stream(
                    model=settings.model, contents=contents, config=config
                )
            )
            return ProviderResponse(text, settings.model)

        return gemini_request

    if settings.provider != "openrouter":
        raise ValueError(f"Unsupported LLM provider: {settings.provider}")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and not dry_run:
        raise RuntimeError("OPENROUTER_API_KEY is required for OpenRouter summaries.")
    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key or "dry-run")

    def openrouter_request(_batch: list[dict], input_text: str) -> ProviderResponse:
        response = client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": input_text}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": (
                        "rss_morning_executive_summary"
                        if executive
                        else "rss_morning_summary"
                    ),
                    "strict": True,
                    "schema": (
                        EXECUTIVE_SUMMARY_JSON_SCHEMA
                        if executive
                        else SUMMARY_JSON_SCHEMA
                    ),
                },
            },
            temperature=0,
            timeout=settings.request_timeout_seconds,
            extra_body={
                "models": list(settings.fallback_models),
                "provider": {
                    "require_parameters": settings.require_structured_output,
                    "sort": settings.routing,
                    "max_price": {
                        "prompt": settings.max_input_price_per_million,
                        "completion": settings.max_output_price_per_million,
                    },
                },
            },
        )
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            response.choices[0].message.content or "",
            str(getattr(response, "model", settings.model)),
            _usage_value(usage, "prompt_tokens", "input_tokens"),
            _usage_value(usage, "completion_tokens", "output_tokens"),
        )

    return openrouter_request


def _settings_from_legacy(
    settings: SummarySettings | None,
    provider: str | None,
    model: str | None,
    batch_size: int | None,
) -> SummarySettings:
    if settings is not None:
        return settings
    resolved_provider = (provider or "openrouter").strip().lower()
    resolved_model = model or (
        "gemini-flash-latest"
        if resolved_provider == "gemini"
        else DEFAULT_OPENROUTER_MODEL
    )
    fallbacks = (
        DEFAULT_OPENROUTER_FALLBACKS
        if resolved_provider == "openrouter" and model is None
        else ()
    )
    defaults = SummarySettings()
    return SummarySettings(
        provider=resolved_provider,
        model=resolved_model,
        fallback_models=fallbacks,
        max_batch_articles=batch_size or defaults.max_batch_articles,
    )


def generate_summary(
    articles: list[dict],
    system_prompt: str,
    return_dict: bool = False,
    batch_size: int | None = None,
    dry_run: bool = False,
    provider: str | None = None,
    model: str | None = None,
    *,
    settings: SummarySettings | None = None,
    provider_request: Callable[[list[dict], str], ProviderResponse] | None = None,
    executive_request: Callable[[list[dict], str], ProviderResponse] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    cache_get: Callable[[str], str | None] | None = None,
    cache_put: Callable[[str, str], None] | None = None,
    metrics_sink: Callable[[dict], None] | None = None,
    stats: RunStats | None = None,
) -> str | tuple[str, dict | None]:
    """Generate validated summary JSON with bounded retry, splitting, and caching."""
    if not articles:
        empty: dict = {"summaries": []}
        rendered = json.dumps(empty, ensure_ascii=False)
        return (rendered, empty) if return_dict else rendered
    resolved = _settings_from_legacy(settings, provider, model, batch_size)
    request = provider_request or _make_provider_request(resolved, dry_run=dry_run)
    synthesis_request = executive_request
    if synthesis_request is None and provider_request is None:
        synthesis_request = _make_provider_request(
            resolved, dry_run=dry_run, executive=True
        )
    batches = plan_batches(
        articles,
        system_prompt,
        max_articles=resolved.max_batch_articles,
        max_input_tokens=resolved.max_input_tokens,
    )
    if stats is not None:
        stats.record_llm(
            planned_batches=len(batches) + int(synthesis_request is not None),
            submitted_token_estimate=sum(
                estimate_input_tokens(system_prompt, batch) for batch in batches
            ),
        )
    if dry_run:
        for index, batch in enumerate(batches, start=1):
            input_text = (
                f"{system_prompt}\n\n{RELEVANCE_DECISION_INSTRUCTION}\n\n"
                f"{build_summary_input(batch)}"
            )
            logger.info("DRY RUN: Prepared payload for batch %d.", index)
            logger.debug("DRY RUN: LLM request payload: %s", input_text)
        logger.info("DRY RUN: skipping API call.")
        result: dict = {"dry_run": True}
        rendered = json.dumps(result)
        return (rendered, result) if return_dict else rendered

    combined: list[dict] = []

    def process(batch: list[dict], depth: int) -> None:
        key = cache_identity(resolved, system_prompt, batch)
        if resolved.cache_enabled and cache_get:
            try:
                cached = cache_get(key)
                if cached:
                    valid = reconcile_response(json.loads(cached), batch)
                    combined.extend(valid["summaries"])
                    if stats is not None:
                        stats.record_llm(exact_cache_hits=1)
                    return
            except Exception:
                logger.warning("Ignoring unreadable LLM batch cache entry")

        last_error: Exception | None = None
        for attempt in range(resolved.max_attempts):
            try:
                input_text = (
                    f"{system_prompt}\n\n{RELEVANCE_DECISION_INSTRUCTION}\n\n"
                    f"{build_summary_input(batch)}"
                )
                logger.debug(
                    "%s request prepared for %d article(s)",
                    resolved.provider,
                    len(batch),
                )
                logger.debug("%s request payload: %s", resolved.provider, input_text)
                if stats is not None:
                    stats.record_llm(provider_calls=1)
                response = request(batch, input_text)
                if response.model not in {resolved.model, *resolved.fallback_models}:
                    raise ValueError(
                        "Provider returned a model outside the configured chain"
                    )
                try:
                    parsed = json.loads(response.text)
                except json.JSONDecodeError as exc:
                    raise ResponseValidationError(
                        "LLM response is not valid JSON"
                    ) from exc
                valid = reconcile_response(parsed, batch)
                combined.extend(valid["summaries"])
                if stats is not None:
                    stats.record_llm(
                        provider_input_tokens=response.input_tokens,
                        provider_output_tokens=response.output_tokens,
                    )
                if metrics_sink:
                    metrics_sink(
                        {
                            "model": response.model,
                            "input_tokens": response.input_tokens,
                            "output_tokens": response.output_tokens,
                        }
                    )
                if resolved.cache_enabled and cache_put:
                    try:
                        cache_put(
                            key,
                            json.dumps(
                                parsed, ensure_ascii=False, separators=(",", ":")
                            ),
                        )
                    except Exception:
                        logger.warning("Failed to write LLM batch cache")
                return
            except Exception as exc:  # noqa: BLE001 - provider boundary
                last_error = exc
                if not _is_transient(exc) or attempt + 1 >= resolved.max_attempts:
                    break
                if stats is not None:
                    stats.record_llm(retries=1)
                sleeper(_retry_delay(exc, attempt, jitter))
        logger.warning(
            "LLM batch failed after bounded attempts: %s", type(last_error).__name__
        )
        if len(batch) > 1 and depth < resolved.max_split_depth:
            if stats is not None:
                stats.record_llm(split_recoveries=1)
            middle = len(batch) // 2
            process(batch[:middle], depth + 1)
            process(batch[middle:], depth + 1)

    for batch in batches:
        process(batch, 0)

    final: dict = {"summaries": combined}
    if combined and synthesis_request is not None:
        key = executive_cache_identity(resolved, combined)
        executive_summary: str | None = None
        if resolved.cache_enabled and cache_get:
            try:
                cached = cache_get(key)
                if cached:
                    executive_summary = reconcile_executive_response(json.loads(cached))
                    if stats is not None:
                        stats.record_llm(exact_cache_hits=1)
            except Exception:
                logger.warning("Ignoring unreadable executive summary cache entry")

        if executive_summary is None:
            input_text = (
                f"{EXECUTIVE_SUMMARY_PROMPT}\n\n"
                f"{build_executive_summary_input(combined)}"
            )
            if stats is not None:
                stats.record_llm(submitted_token_estimate=_estimated_tokens(input_text))
            last_error: Exception | None = None
            for attempt in range(resolved.max_attempts):
                try:
                    logger.debug(
                        "%s executive synthesis request prepared for %d summary item(s)",
                        resolved.provider,
                        len(combined),
                    )
                    logger.debug(
                        "%s executive synthesis request payload: %s",
                        resolved.provider,
                        input_text,
                    )
                    if stats is not None:
                        stats.record_llm(provider_calls=1)
                    response = synthesis_request(combined, input_text)
                    if response.model not in {
                        resolved.model,
                        *resolved.fallback_models,
                    }:
                        raise ValueError(
                            "Provider returned a model outside the configured chain"
                        )
                    try:
                        parsed = json.loads(response.text)
                    except json.JSONDecodeError as exc:
                        raise ResponseValidationError(
                            "Executive summary response is not valid JSON"
                        ) from exc
                    executive_summary = reconcile_executive_response(parsed)
                    if stats is not None:
                        stats.record_llm(
                            provider_input_tokens=response.input_tokens,
                            provider_output_tokens=response.output_tokens,
                        )
                    if metrics_sink:
                        metrics_sink(
                            {
                                "model": response.model,
                                "input_tokens": response.input_tokens,
                                "output_tokens": response.output_tokens,
                            }
                        )
                    if resolved.cache_enabled and cache_put:
                        try:
                            cache_put(
                                key,
                                json.dumps(
                                    {"executive-summary": executive_summary},
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            )
                        except Exception:
                            logger.warning("Failed to write executive summary cache")
                    break
                except Exception as exc:  # noqa: BLE001 - provider boundary
                    last_error = exc
                    if not _is_transient(exc) or attempt + 1 >= resolved.max_attempts:
                        break
                    if stats is not None:
                        stats.record_llm(retries=1)
                    sleeper(_retry_delay(exc, attempt, jitter))
            if executive_summary is None:
                logger.warning(
                    "Executive synthesis failed after bounded attempts: %s",
                    type(last_error).__name__,
                )

        if executive_summary:
            final["exec_summary"] = executive_summary
    rendered = json.dumps(final, ensure_ascii=False, indent=2)
    if not combined:
        logger.warning("No summaries were generated from any batch.")
    return (rendered, final) if return_dict else rendered
