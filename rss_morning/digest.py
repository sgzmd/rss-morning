"""Editorial digest generation using OpenRouter (Pyramid Principle).

Structure of an edition:
1. Executive Summary: 1 bottom-line sentence + 3-5 grounded key points linking to stories.
2. Valuable Topics: cohesive cross-cutting themes synthesizing stories, plus story cards.
3. Deep Dives: comprehensive technical breakdowns for the highest-impact stories,
   synthesized from full article texts in parallel.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .config import DigestConfig
from .models import AreaConfig, TechnologyFootprint
from .openrouter import complete_structured

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strict JSON Schemas for OpenRouter Structured Outputs
# ---------------------------------------------------------------------------


def build_editor_schema(area_keys: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Schema for Story clustering, topic synthesis, and executive summary."""
    primary_area_prop: Dict[str, Any] = {
        "type": "string",
        "description": "Primary focus area chosen from the configured taxonomy",
    }
    if area_keys:
        primary_area_prop["enum"] = list(area_keys)

    return {
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "description": "All deduplicated and clustered stories selected for today's briefing.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique story identifier, e.g. s1, s2, s3.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Crisp, specific headline for the story.",
                        },
                        "primary_area": primary_area_prop,
                        "tier": {
                            "type": "string",
                            "enum": ["critical", "important", "notable"],
                            "description": "Editorial urgency tier.",
                        },
                        "exploitation_status": {
                            "type": "string",
                            "enum": [
                                "confirmed_in_the_wild",
                                "public_poc",
                                "not_reported",
                                "not_applicable",
                            ],
                            "description": "Grounded exploitation status based strictly on the source text.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "3-5 sentence sharp, engaging summary in the Risky Business style: direct, active voice, zero corporate filler. Detail the actor, exploit mechanism, root cause, and practical blast radius.",
                        },
                        "key_facts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key factual bullets (CVEs, CVSS scores, affected software/versions, threat actor, attributed agency).",
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": "Sharp, realistic takeaway on real-world fallout and practitioner impact, cutting through vendor hype, without assuming internal environment details.",
                        },
                        "article_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "IDs of candidate articles (e.g. art-1, art-5) that cover this story.",
                        },
                    },
                    "required": [
                        "id",
                        "title",
                        "primary_area",
                        "tier",
                        "exploitation_status",
                        "summary",
                        "key_facts",
                        "why_it_matters",
                        "article_ids",
                    ],
                    "additionalProperties": False,
                },
            },
            "topics": {
                "type": "array",
                "description": "Cohesive overarching themes grouping related stories (e.g. 'Perimeter Infrastructure Under Active Attack').",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique topic identifier, e.g. t1, t2.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Descriptive, high-signal topic title.",
                        },
                        "synthesis": {
                            "type": "string",
                            "description": "1 sharp, engaging paragraph synthesizing the cross-cutting pattern, threat dynamics, or systemic failure without corporate jargon.",
                        },
                        "story_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Story IDs belonging to this topic.",
                        },
                    },
                    "required": ["id", "title", "synthesis", "story_ids"],
                    "additionalProperties": False,
                },
            },
            "executive_summary": {
                "type": "object",
                "description": "Executive summary written with full view of all clustered stories and topics.",
                "properties": {
                    "bottom_line": {
                        "type": "string",
                        "description": "Single punchy, memorable takeaway sentence capturing the unvarnished reality of today's landscape in the Risky Business style.",
                    },
                    "key_points": {
                        "type": "array",
                        "description": "3-5 key priority points for practitioners and leaders.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "1-2 sentence high-impact summary of a key development.",
                                },
                                "story_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Story IDs related to this point.",
                                },
                            },
                            "required": ["text", "story_ids"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["bottom_line", "key_points"],
                "additionalProperties": False,
            },
        },
        "required": [
            "stories",
            "topics",
            "executive_summary",
        ],
        "additionalProperties": False,
    }


# Backward compatibility alias
build_edition_schema = build_editor_schema
EDITION_SCHEMA: Dict[str, Any] = build_editor_schema()


# ---------------------------------------------------------------------------
# Editorial Policy Prompts
# ---------------------------------------------------------------------------

DEFAULT_EDITORIAL_POLICY = """You are the edition editor for a high-signal security intelligence morning briefing.
Your goal is to synthesize incoming articles into a sharp, deeply factual, practitioner-oriented briefing.

Voice & Tone Mandate ("Risky Business" Podcast Style):
- CHANNEL RISKY BUSINESS: Write in the sharp, technical, and engaging voice of the Risky Business podcast. Be authoritative, direct, and conversational—never dry, bureaucratic, or academic.
- ZERO CORPORATE FLUFF: Ban boilerplate clichés like "in today's evolving threat landscape", "serves as a stark reminder", "organizations are urged to patch", or "cyber hygiene is paramount".
- SKEPTICAL OF VENDOR SPIN: Strip away PR euphemisms and marketing hype. If a vendor calls an unauthenticated remote code execution bug an "inadvertent exposure" or buries an actively exploited zero-day in routine release notes, call it what it actually is. Focus on the actual exploit mechanics, root cause, architectural failure, and real-world blast radius.
- PUNCHY & ENGAGING: Use active voice and crisp phrasing. Explain technical nuances clearly without dumbing them down. A touch of wry realism regarding threat actor blunders or vendor missteps is encouraged, but stay 100% grounded in factual reality.
- PRACTITIONER-FIRST: Focus on what engineers and security leaders actually care about: Is this being actively abused in the wild, is there a working PoC, or is it just academic research?

Non-Negotiable Editorial Rules:
1. STRICT GROUNDING: Only assert facts, metrics, CVEs, CVSS scores, threat actor names, and impacts explicitly documented in the provided source texts. Do not extrapolate, speculate, or invent details.
2. BAN INTERNAL-ENVIRONMENT CLAIMS: NEVER state, assume, or imply that our organization, readers, or any specific company uses, deploys, or operates a technology simply because an article discusses a vulnerability or product in that technology (e.g. if an article describes a FortiMail or NetScaler bug, do NOT claim 'we use FortiMail' or 'our NetScaler devices'). Frame observations strictly as industry or vendor facts.
3. PRESERVE UNCERTAINTY: Clearly distinguish confirmed active in-the-wild exploitation from theoretical proof-of-concepts, routine vendor patches, or academic research. If active exploitation is unconfirmed or disputed, state that uncertainty explicitly.
4. COLLAPSE DUPLICATES: When multiple articles report on the same underlying security incident, campaign, or CVE, synthesize them into a SINGLE story and list all their article IDs in `article_ids`.
5. EDITORIAL TRIAGE:
   - "critical": Actively exploited zero-days, massive supply chain breaches, critical infrastructure compromises, severe unauthenticated remote code execution.
   - "important": Major vendor patch drops with high-severity flaws, widespread active phishing campaigns, critical security research, regulatory mandates.
   - "notable": Emerging trends, low-to-medium vulnerabilities, routine updates, notable policy updates.
   - Low-value articles, duplicate re-blogs of minor news, or vendor marketing fluff should be omitted entirely. Do not force every article into the digest.
"""

DEFAULT_EDITORIAL_PROMPT = DEFAULT_EDITORIAL_POLICY  # Backward compatibility alias

STRUCTURE_INSTRUCTIONS = """
Structural Instructions (Pyramid Principle):
1. STORIES: First, cluster the incoming articles into deduplicated stories. For each story, provide:
   - `summary`: 3-5 sentence substantive, engaging summary in the Risky Business style (direct, active voice, exploit mechanics, attacker, and operational impact).
   - `key_facts`: 2-4 key fact bullets (CVEs, CVSS, software versions, actor names).
   - `why_it_matters`: Grounded, sharp assessment of why defenders must care, without assuming internal environment details.
   - Reference the source article IDs in `article_ids`.
2. TOPICS: Next, identify 2 to 6 cross-cutting thematic topics grouping related stories (e.g. 'Edge Perimeter Appliances Under Fire', 'Passkey Ecosystem Friction and Attacks'). For each topic, write a sharp 1-paragraph synthesis exposing the systemic pattern, threat dynamics, or attacker economics, and reference member story IDs.
3. EXECUTIVE SUMMARY: Finally, synthesize the entire edition into:
   - `bottom_line`: Exactly 1 punchy, memorable sentence capturing the overarching reality of today's landscape.
   - `key_points`: 3 to {max_exec_points} prioritized bullet points (1-2 sentences each), linking to their relevant `story_ids`.
"""


# ---------------------------------------------------------------------------
# Formatting Candidate Articles for LLM
# ---------------------------------------------------------------------------


def build_editorial_input(
    articles: List[dict], char_limit: Optional[int] = None
) -> str:
    """Format candidate articles with metadata and classifications for the LLM."""
    items = []
    for index, article in enumerate(articles, start=1):
        art_id = article.get("id") or f"art-{index}"
        raw_text = (article.get("text") or "").strip()
        if char_limit and len(raw_text) > char_limit:
            chunk = raw_text[:char_limit]
            if " " in chunk:
                chunk = chunk.rsplit(" ", 1)[0]
            raw_text = chunk

        items.append(
            {
                "id": art_id,
                "title": (article.get("title") or "").strip(),
                "url": (article.get("url") or "").strip(),
                "primary_area": article.get("primary_area")
                or article.get("category")
                or "other",
                "summary": (article.get("summary") or "").strip(),
                "content": raw_text,
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ID Resolution & View Model Deterministic Assembly
# ---------------------------------------------------------------------------


def resolve_edition_ids(
    editor_data: Dict[str, Any],
    articles: List[dict],
) -> Dict[str, Any]:
    """Map article IDs to URLs and resolve relationships safely."""
    # Build article lookup by assigned ID and by 1-based index art-N
    article_by_id: Dict[str, dict] = {}
    for index, art in enumerate(articles, start=1):
        assigned_id = art.get("id") or f"art-{index}"
        article_by_id[assigned_id] = art
        article_by_id[f"art-{index}"] = art

    raw_stories = list(editor_data.get("stories") or [])
    resolved_stories: List[Dict[str, Any]] = []
    stories_by_id: Dict[str, Dict[str, Any]] = {}

    for story in raw_stories:
        if not isinstance(story, dict):
            continue
        story_id = str(story.get("id") or "").strip()
        if not story_id:
            continue

        raw_art_ids = story.get("article_ids") or []
        resolved_urls: List[str] = []
        valid_art_ids: List[str] = []
        seen_urls = set()

        for aid in raw_art_ids:
            art = article_by_id.get(str(aid).strip())
            if art and art.get("url"):
                u = art["url"].strip()
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    resolved_urls.append(u)
                    valid_art_ids.append(str(aid).strip())

        story_copy = dict(story)
        story_copy["id"] = story_id
        story_copy["source_urls"] = resolved_urls
        story_copy["article_ids"] = valid_art_ids
        resolved_stories.append(story_copy)
        stories_by_id[story_id] = story_copy

    # Resolve topics
    raw_topics = list(editor_data.get("topics") or [])
    resolved_topics: List[Dict[str, Any]] = []
    for topic in raw_topics:
        if not isinstance(topic, dict):
            continue
        valid_sids = [
            sid
            for sid in (topic.get("story_ids") or [])
            if str(sid).strip() in stories_by_id
        ]
        topic_copy = dict(topic)
        topic_copy["story_ids"] = valid_sids
        resolved_topics.append(topic_copy)

    # Resolve executive summary
    raw_exec = editor_data.get("executive_summary") or {}
    key_points = []
    for pt in raw_exec.get("key_points") or []:
        if not isinstance(pt, dict):
            continue
        valid_sids = [
            sid
            for sid in (pt.get("story_ids") or [])
            if str(sid).strip() in stories_by_id
        ]
        key_points.append(
            {
                "text": str(pt.get("text") or "").strip(),
                "story_ids": valid_sids,
            }
        )

    resolved_exec = {
        "bottom_line": str(raw_exec.get("bottom_line") or "").strip(),
        "key_points": key_points,
    }

    return {
        "stories": resolved_stories,
        "topics": resolved_topics,
        "executive_summary": resolved_exec,
    }


# ---------------------------------------------------------------------------
# Main Digest Generation
# ---------------------------------------------------------------------------


def generate_digest(
    articles: List[dict],
    system_prompt: Optional[str] = None,
    dry_run: bool = False,
    model: Optional[str] = None,
    areas: Optional[Mapping[str, AreaConfig]] = None,
    digest_config: Optional[DigestConfig] = None,
    reasoning_effort: Optional[str] = "low",
    technologies: Optional[TechnologyFootprint] = None,
) -> Dict[str, Any]:
    """Generate an editorial digest edition using OpenRouter."""
    cfg = digest_config or DigestConfig()

    if not articles:
        logger.info(
            "No articles provided for edition generation; returning empty edition."
        )
        return {
            "executive_summary": {
                "bottom_line": "No articles retrieved for this edition.",
                "key_points": [],
            },
            "topics": [],
            "stories": [],
            # Backward-compat aliases
            "overview": "No articles retrieved for this edition.",
            "attention": [],
            "watch": [],
        }

    # Ensure each article has an assigned art-N ID
    prepared_articles: List[dict] = []
    article_lookup: Dict[str, dict] = {}
    for index, art in enumerate(articles, start=1):
        art_copy = dict(art)
        art_id = art_copy.get("id") or f"art-{index}"
        art_copy["id"] = art_id
        prepared_articles.append(art_copy)
        article_lookup[art_id] = art_copy

    area_keys = list(areas.keys()) if areas else None
    editor_schema = build_editor_schema(area_keys)

    # Compose the system prompt:
    # 1. Editorial policy (user custom or default Risky Business style)
    # 2. Structural instructions
    # 3. Focus area definitions and prioritized technologies
    policy_prompt = (system_prompt or DEFAULT_EDITORIAL_POLICY).strip()
    structural_prompt = STRUCTURE_INSTRUCTIONS.format(
        max_exec_points=cfg.exec_summary_points,
    ).strip()

    prompt_parts = [policy_prompt, structural_prompt]
    if areas:
        lines = []
        for k, v in areas.items():
            desc_parts = [f"   - {k} ({v.label}): {v.description}"]
            if getattr(v, "priority", None) == "high":
                desc_parts.append("     *PRIORITY FOCUS*: High priority topic.")
            if getattr(v, "technologies", None):
                tech_str = ", ".join(v.technologies)
                desc_parts.append(f"     *Target Technologies*: {tech_str}")
            lines.append("\n".join(desc_parts))
        areas_desc = "\n".join(lines)
        prompt_parts.append(
            f"CONFIGURED FOCUS AREAS & TARGET TECHNOLOGIES:\nAssign each story's primary_area strictly to one of these configured areas:\n{areas_desc}"
        )

    # Technology footprint / priority carveout
    if technologies:
        tech_lines = []
        if technologies.description:
            tech_lines.append(technologies.description)
        if technologies.technologies:
            tech_lines.append(
                "Prioritized Technologies:\n"
                + "\n".join(f"- {t}" for t in technologies.technologies)
            )
        tech_summary = "\n".join(tech_lines)
        prompt_parts.append(
            f"TECHNOLOGY FOOTPRINT & EDITORIAL PRIORITY DIRECTIVE:\n"
            f"{tech_summary}\n\n"
            f"PRIORITY RULE: Stories that directly affect our specific technologies—in particular:\n"
            f"  1. Mobile Security (Android and iOS platforms, mobile app security, client integrity, sandboxing)\n"
            f"  2. Consumer Authentication & Passkeys (Passkeys, FIDO2, WebAuthn, consumer biometric login, credential theft defenses)\n"
            f"MUST be given higher editorial priority. Elevate their tier ('critical' or 'important') and feature them prominently."
        )

    full_editor_prompt = "\n\n".join(prompt_parts)

    user_content = build_editorial_input(
        prepared_articles, char_limit=cfg.editor_article_chars
    )

    if dry_run:
        logger.info(
            "DRY RUN: Prepared editorial payload for %d articles",
            len(prepared_articles),
        )
        sample_story = {
            "id": "s1",
            "title": f"Dry Run Sample Story ({len(prepared_articles)} articles)",
            "primary_area": area_keys[0] if area_keys else "other",
            "tier": "important",
            "exploitation_status": "not_reported",
            "summary": "Dry run summary of incoming articles in Risky Business style.",
            "key_facts": ["Dry run execution"],
            "why_it_matters": "Demonstrates digest structure.",
            "article_ids": ["art-1"],
            "source_urls": [prepared_articles[0].get("url") or "https://example.com"],
        }
        return {
            "executive_summary": {
                "bottom_line": f"Dry run overview for {len(prepared_articles)} articles.",
                "key_points": [
                    {
                        "text": "Dry run key priority point.",
                        "story_ids": ["s1"],
                    }
                ],
            },
            "topics": [
                {
                    "id": "t1",
                    "title": "Dry Run Topic",
                    "synthesis": "Synthesis of dry run topic.",
                    "story_ids": ["s1"],
                }
            ],
            "stories": [sample_story],
            # Backward-compat aliases
            "overview": f"Dry run overview for {len(prepared_articles)} articles.",
            "attention": [sample_story],
            "watch": [],
        }

    logger.info(
        "Calling OpenRouter editor for %d articles",
        len(prepared_articles),
    )
    reasoning_payload = {"effort": reasoning_effort} if reasoning_effort else None

    stage1_response = complete_structured(
        messages=[
            {"role": "system", "content": full_editor_prompt},
            {"role": "user", "content": user_content},
        ],
        json_schema=editor_schema,
        schema_name="editorial_edition",
        model=model,
        timeout=180.0,
        reasoning=reasoning_payload,
    )

    resolved = resolve_edition_ids(stage1_response, prepared_articles)

    # Backward compatibility projections (overview, attention, watch)
    overview_text = resolved["executive_summary"].get("bottom_line") or ""
    attention_list = [s for s in resolved["stories"] if s.get("tier") == "critical"]
    watch_list = [s for s in resolved["stories"] if s.get("tier") != "critical"]

    logger.info(
        "Generated editorial edition: %d stories, %d topics",
        len(resolved["stories"]),
        len(resolved["topics"]),
    )

    return {
        "executive_summary": resolved["executive_summary"],
        "topics": resolved["topics"],
        "stories": resolved["stories"],
        # Backward compatibility fields
        "overview": overview_text,
        "attention": attention_list,
        "watch": watch_list,
    }
