"""Editorial digest generation using OpenRouter."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .openrouter import complete_structured

logger = logging.getLogger(__name__)

EDITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": {
            "type": "string",
            "description": "Concise executive overview of today's security landscape (1 paragraph).",
        },
        "attention": {
            "type": "array",
            "description": "Stories warranting proactive attention with grounded urgency rationale.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Concise headline for the story",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Factual grounded summary of the event, strictly preserving uncertainty.",
                    },
                    "source_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Source URLs covering this event (collapsed if multiple)",
                    },
                    "primary_area": {
                        "type": "string",
                        "description": "Primary security area",
                    },
                    "urgency_rationale": {
                        "type": "string",
                        "description": "Why this warrants proactive attention, strictly grounded in the source text without assuming internal environment details.",
                    },
                },
                "required": [
                    "title",
                    "summary",
                    "source_urls",
                    "primary_area",
                    "urgency_rationale",
                ],
                "additionalProperties": False,
            },
        },
        "watch": {
            "type": "array",
            "description": "Lower-urgency or emerging items to monitor.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Concise headline for the story",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Factual 1-2 sentence summary of what occurred.",
                    },
                    "source_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Source URLs covering this event",
                    },
                    "primary_area": {
                        "type": "string",
                        "description": "Primary security area",
                    },
                },
                "required": ["title", "summary", "source_urls", "primary_area"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "attention", "watch"],
    "additionalProperties": False,
}

DEFAULT_EDITORIAL_PROMPT = """You are the edition editor for an executive cyber security morning briefing.
Your goal is to synthesize incoming security articles into a concise, high-signal briefing.

You must adhere strictly to these core editorial rules:

1. STRICT GROUNDING: Only assert facts, metrics, and impacts explicitly documented in the provided source texts. Do not extrapolate, speculate, or invent details.
2. BAN INTERNAL-ENVIRONMENT CLAIMS: NEVER state, assume, or imply that our organization, readers, or any specific company uses, deploys, or operates a technology simply because an article discusses a vulnerability or product in that technology (e.g., if an article describes a NetScaler or OpenVPN bug, do NOT claim 'we use NetScaler' or 'OpenVPN is used for our workforce'). Frame observations strictly as industry or vendor facts.
3. PRESERVE UNCERTAINTY: Clearly distinguish between actively exploited zero-days/in-the-wild attacks vs. theoretical proof-of-concepts, routine patches, or academic research. If active exploitation is unconfirmed, say so clearly.
4. COLLAPSE DUPLICATES: When multiple articles report on the same underlying security incident, campaign, or CVE, synthesize them into a SINGLE item and combine all their source URLs into `source_urls`.
5. EDITORIAL TRIAGE:
   - "overview": Exactly one concise paragraph summarizing key themes or tone of today's security developments.
   - "attention": High-severity stories that demand immediate awareness (e.g., critical zero-days under active exploitation, severe systemic supply chain breaches, major regulatory actions). Each item MUST include a source-grounded `urgency_rationale`. If nothing warrants immediate attention, return an empty array.
   - "watch": Important but lower-urgency items, emerging trends, or notable patches worth tracking.
   - Articles that are low value, duplicates of minor news, or irrelevant marketing fluff should be omitted entirely. Do not force every article into the digest.
"""


def build_editorial_input(articles: List[dict]) -> str:
    """Format candidate articles with metadata and Jev classifications for the LLM."""
    items = []
    for index, article in enumerate(articles, start=1):
        items.append(
            {
                "id": f"art-{index}",
                "title": (article.get("title") or "").strip(),
                "url": (article.get("url") or "").strip(),
                "primary_area": article.get("primary_area")
                or article.get("category")
                or "other",
                "summary": (article.get("summary") or "").strip(),
                "content": (article.get("text") or "").strip(),
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=2)


def generate_digest(
    articles: List[dict],
    system_prompt: Optional[str] = None,
    dry_run: bool = False,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a single editorial digest edition using OpenRouter."""
    if not articles:
        logger.info(
            "No articles provided for edition generation; returning empty edition."
        )
        return {
            "overview": "No articles retrieved for this edition.",
            "attention": [],
            "watch": [],
        }

    prompt = system_prompt or DEFAULT_EDITORIAL_PROMPT
    user_content = build_editorial_input(articles)

    if dry_run:
        logger.info(
            "DRY RUN: Prepared editorial payload for %d articles", len(articles)
        )
        return {
            "overview": f"Dry run overview for {len(articles)} articles.",
            "attention": [],
            "watch": [],
        }

    logger.info("Calling OpenRouter edition editor for %d articles", len(articles))
    response = complete_structured(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        json_schema=EDITION_SCHEMA,
        schema_name="editorial_edition",
        model=model,
    )

    overview = str(response.get("overview") or "").strip()
    attention = list(response.get("attention") or [])
    watch = list(response.get("watch") or [])

    logger.info(
        "Generated editorial edition: overview length %d, attention %d, watch %d",
        len(overview),
        len(attention),
        len(watch),
    )
    return {
        "overview": overview,
        "attention": attention,
        "watch": watch,
    }
