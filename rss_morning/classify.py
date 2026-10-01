"""Article relevance and area classification via TypeSafe Jev (System One)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

TYPESAFE_API_URL = (
    os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
    + "/v1/systemone"
)

DEFAULT_MODEL = "jev-latest"
DEFAULT_RELEVANCE_THRESHOLD = 0.50

# Centralised primary area taxonomy
DEFAULT_AREAS: Dict[str, str] = {
    "account_security": (
        "Account takeover, authentication, credentials, passkeys, MFA, "
        "session security, and customer identity"
    ),
    "mobile_security": (
        "Mobile application security, Android and iOS vulnerabilities, "
        "mobile malware, device attestation, and client integrity"
    ),
    "corporate_it": (
        "Enterprise networking, VPN, SD-WAN, firewalls, identity providers, "
        "managed devices, browsers, and workplace IT infrastructure"
    ),
    "fraud_abuse": (
        "Financial fraud, botting, spam campaigns, credential stuffing, "
        "payment abuse, and malicious automation"
    ),
    "ai_security": (
        "AI model vulnerabilities, prompt injection, AI agent safety, "
        "and security implications of LLMs and generative AI"
    ),
    "privacy_regulation": (
        "Data privacy, compliance regulations, breach reporting mandates, "
        "legal enforcement, and privacy frameworks"
    ),
    "other": ("Does not fit any security area above, or general non-security material"),
}

RELEVANCE_QUESTION: Dict[str, Any] = {
    "type": "noul",
    "instructions": (
        "Is this article plausibly relevant enough to a security briefing "
        "that we should spend the effort to read the full article?"
    ),
    "criteria": {
        "true": (
            "Discusses cyber security, software vulnerabilities, threat campaigns, "
            "identity, enterprise IT security, cloud security, privacy, or security research"
        ),
        "false": (
            "General technology news, consumer gadgets, non-security business news, "
            "lifestyle, general programming tutorials, or off-topic material"
        ),
    },
}


class TypeSafeError(RuntimeError):
    """Raised when an evaluation call to TypeSafe System One fails."""


@dataclass(frozen=True)
class ClassificationDecision:
    """Deterministic classification outcome for an article."""

    relevance_probability: float
    primary_area: str
    area_confidence: float
    is_plausible: bool


def build_jev_questions(
    areas: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Construct centralized Jev question schema for relevance and primary area."""
    configured_areas = dict(areas or DEFAULT_AREAS)
    if "other" not in configured_areas:
        configured_areas["other"] = "Does not fit any category above"

    return {
        "is_relevant": RELEVANCE_QUESTION,
        "primary_area": {
            "type": "choice",
            "instructions": "What primary security area does this article describe?",
            "criteria": configured_areas,
        },
    }


def classify_entry(
    entry_metadata: Mapping[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    api_key: Optional[str] = None,
    areas: Optional[Mapping[str, str]] = None,
    timeout: float = 15.0,
) -> ClassificationDecision:
    """Submit cheap feed metadata to TypeSafe Jev for relevance and area judgement."""
    resolved_key = api_key or os.environ.get("TYPESAFE_API_KEY")
    if not resolved_key:
        raise TypeSafeError("TYPESAFE_API_KEY environment variable is not set.")

    # Prepare cheap metadata state (no full article body)
    state = {
        "title": entry_metadata.get("title", ""),
        "summary": entry_metadata.get("summary", ""),
        "feed_title": entry_metadata.get("feed_title", "")
        or entry_metadata.get("category", ""),
        "url": entry_metadata.get("link") or entry_metadata.get("url", ""),
    }

    questions = build_jev_questions(areas=areas)

    headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "state": state,
        "questions": questions,
    }

    masked_key = f"...{resolved_key[-4:]}" if len(resolved_key) >= 4 else "****"
    logger.debug(
        "Calling TypeSafe Jev model=%s for title=%r with key=%s",
        model,
        state["title"][:50],
        masked_key,
    )

    try:
        response = requests.post(
            TYPESAFE_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise TypeSafeError(
            f"TypeSafe API request timed out after {timeout}s: {exc}"
        ) from exc
    except requests.RequestException as exc:
        raise TypeSafeError(f"TypeSafe API network error: {exc}") from exc

    if response.status_code != 200:
        error_detail = response.text[:200]
        try:
            err_json = response.json()
            if "error" in err_json:
                error_detail = err_json["error"]
        except Exception:
            pass
        raise TypeSafeError(
            f"TypeSafe API returned HTTP {response.status_code}: {error_detail}"
        )

    try:
        data = response.json()
        answers = data["answers"]
        rel_noul = answers["is_relevant"]["noul"]
        relevance_prob = float(rel_noul)

        choice_ans = answers["primary_area"]
        primary_area = str(choice_ans["choice"])
        confidence = float(choice_ans.get("confidence", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise TypeSafeError(
            f"Failed to parse TypeSafe response payload: {exc}"
        ) from exc

    is_plausible = relevance_prob >= threshold

    return ClassificationDecision(
        relevance_probability=relevance_prob,
        primary_area=primary_area,
        area_confidence=confidence,
        is_plausible=is_plausible,
    )


def classify_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    api_key: Optional[str] = None,
    areas: Optional[Mapping[str, str]] = None,
    timeout: float = 15.0,
) -> List[tuple[Mapping[str, Any], ClassificationDecision]]:
    """Classify a sequence of feed entries, returning pairs of (entry, decision)."""
    results = []
    for entry in entries:
        decision = classify_entry(
            entry,
            model=model,
            threshold=threshold,
            api_key=api_key,
            areas=areas,
            timeout=timeout,
        )
        results.append((entry, decision))
    return results
