"""Article relevance and area classification via TypeSafe Jev (System One)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

import requests

from .config import DEFAULT_AREAS as CONFIG_DEFAULT_AREAS
from .models import AreaConfig

logger = logging.getLogger(__name__)

TYPESAFE_API_URL = (
    os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
    + "/v1/systemone"
)

DEFAULT_MODEL = "jev-latest"
DEFAULT_RELEVANCE_THRESHOLD = 0.50

DEFAULT_AREAS: Dict[str, str] = {
    k: v.description for k, v in CONFIG_DEFAULT_AREAS.items()
}

RELEVANCE_QUESTION: Dict[str, Any] = {
    "type": "noul",
    "instructions": (
        "Is this article plausibly relevant enough to the briefing "
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
    effective_threshold: float = DEFAULT_RELEVANCE_THRESHOLD


def build_jev_questions(
    areas: Optional[Mapping[str, AreaConfig | str]] = None,
    relevance_instructions: Optional[str] = None,
    relevance_criteria: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Construct centralized Jev question schema for relevance and primary area."""
    criteria: Dict[str, str] = {}
    if areas:
        for k, v in areas.items():
            desc = v.description if hasattr(v, "description") else str(v)
            criteria[k] = desc
    else:
        criteria = dict(DEFAULT_AREAS)

    if "other" not in criteria:
        criteria["other"] = (
            "Does not fit any configured area above, or general non-matching material"
        )

    rel_instructions = relevance_instructions or RELEVANCE_QUESTION["instructions"]
    rel_criteria = (
        dict(relevance_criteria)
        if relevance_criteria
        else dict(RELEVANCE_QUESTION["criteria"])
    )

    return {
        "is_relevant": {
            "type": "noul",
            "instructions": rel_instructions,
            "criteria": rel_criteria,
        },
        "primary_area": {
            "type": "choice",
            "instructions": "What primary area does this article describe?",
            "criteria": criteria,
        },
    }


def classify_entry(
    entry_metadata: Mapping[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    api_key: Optional[str] = None,
    areas: Optional[Mapping[str, AreaConfig | str]] = None,
    relevance_instructions: Optional[str] = None,
    relevance_criteria: Optional[Mapping[str, str]] = None,
    timeout: float = 15.0,
) -> ClassificationDecision:
    """Submit cheap feed metadata to TypeSafe Jev for relevance and area judgement."""
    resolved_key = api_key or os.environ.get("TYPESAFE_API_KEY")
    if not resolved_key:
        raise TypeSafeError("TYPESAFE_API_KEY environment variable is not set.")

    state = {
        "title": entry_metadata.get("title", ""),
        "summary": entry_metadata.get("summary", ""),
        "feed_title": entry_metadata.get("feed_title", "")
        or entry_metadata.get("category", ""),
        "url": entry_metadata.get("link") or entry_metadata.get("url", ""),
    }

    questions = build_jev_questions(
        areas=areas,
        relevance_instructions=relevance_instructions,
        relevance_criteria=relevance_criteria,
    )

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

    # Determine effective threshold for this area
    effective_threshold = float(threshold)
    if areas and primary_area in areas:
        area_item = areas[primary_area]
        if hasattr(area_item, "threshold"):
            effective_threshold = float(area_item.threshold)

    is_plausible = relevance_prob >= effective_threshold

    return ClassificationDecision(
        relevance_probability=relevance_prob,
        primary_area=primary_area,
        area_confidence=confidence,
        is_plausible=is_plausible,
        effective_threshold=effective_threshold,
    )


def classify_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
    api_key: Optional[str] = None,
    areas: Optional[Mapping[str, AreaConfig | str]] = None,
    relevance_instructions: Optional[str] = None,
    relevance_criteria: Optional[Mapping[str, str]] = None,
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
            relevance_instructions=relevance_instructions,
            relevance_criteria=relevance_criteria,
            timeout=timeout,
        )
        results.append((entry, decision))
    return results
