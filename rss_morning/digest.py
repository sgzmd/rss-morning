"""Editorial digest generation using OpenRouter (Pyramid Principle).

Structure of an edition:
1. Executive Summary: 1 bottom-line sentence + 3-5 grounded key points linking to stories.
2. Valuable Topics: cohesive cross-cutting themes synthesizing stories, plus story cards.
3. Deep Dives: comprehensive technical breakdowns for the highest-impact stories,
   synthesized from full article texts in parallel.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .config import DigestConfig
from .models import AreaConfig
from .openrouter import complete_structured

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strict JSON Schemas for OpenRouter Structured Outputs
# ---------------------------------------------------------------------------


def build_editor_schema(area_keys: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Schema for Stage 1: Story clustering, topic synthesis, and executive summary."""
    primary_area_prop: Dict[str, Any] = {
        "type": "string",
        "description": "Primary security area chosen from the configured taxonomy",
    }
    if area_keys:
        primary_area_prop["enum"] = list(area_keys)

    return {
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "description": "All deduplicated and clustered security stories selected for today's briefing.",
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
                            "description": "3-5 sentence substantive summary of the event, actor, mechanism, and impact.",
                        },
                        "key_facts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key factual bullets (CVEs, CVSS scores, affected software/versions, threat actor, attributed agency).",
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": "Grounded urgency rationale explaining why a security leader must care, without assuming internal environment details.",
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
                "description": "Cohesive overarching themes of the day grouping related stories (e.g. 'Perimeter Infrastructure Under Active Attack').",
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
                            "description": "1 substantive paragraph synthesizing the cross-cutting pattern, threat dynamics, or industry significance.",
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
            "deep_dive_story_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered IDs of the top 2-5 highest-impact stories that warrant a technical deep dive.",
            },
            "executive_summary": {
                "type": "object",
                "description": "Executive summary written with full view of all clustered stories and topics.",
                "properties": {
                    "bottom_line": {
                        "type": "string",
                        "description": "Single powerful takeaway sentence capturing the dominant theme of today's landscape.",
                    },
                    "key_points": {
                        "type": "array",
                        "description": "3-5 key priority points for executives.",
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
            "deep_dive_story_ids",
            "executive_summary",
        ],
        "additionalProperties": False,
    }


DEEP_DIVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "what_happened": {
            "type": "string",
            "description": "Detailed explanation of the incident, campaign, or disclosure.",
        },
        "technical_details": {
            "type": "string",
            "description": "Root cause, exploit mechanics, protocols, C2 behavior, or architecture weaknesses explicitly cited in the text.",
        },
        "affected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific products, platforms, versions, models, or organizations confirmed affected.",
        },
        "exploitation_and_evidence": {
            "type": "string",
            "description": "Grounded exploitation status: active in-the-wild exploitation, proof-of-concept, telemetry evidence, CISA KEV listing, or unconfirmed.",
        },
        "timeline": {
            "type": "array",
            "description": "Chronological milestones explicitly mentioned in the sources.",
            "items": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date, timestamp, or approximate timing (e.g. 'October 2, 2026').",
                    },
                    "event": {
                        "type": "string",
                        "description": "Description of the milestone.",
                    },
                },
                "required": ["date", "event"],
                "additionalProperties": False,
            },
        },
        "mitigations_as_reported": {
            "type": "string",
            "description": "Official vendor patches, workarounds, IOCs, or advisory recommendations reported in the sources.",
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Known uncertainties, unconfirmed threat actor attribution, missing patches, or ongoing investigations.",
        },
    },
    "required": [
        "what_happened",
        "technical_details",
        "affected",
        "exploitation_and_evidence",
        "timeline",
        "mitigations_as_reported",
        "open_questions",
    ],
    "additionalProperties": False,
}


# Backward compatibility alias
build_edition_schema = build_editor_schema
EDITION_SCHEMA: Dict[str, Any] = build_editor_schema()


# ---------------------------------------------------------------------------
# Editorial Policy Prompts
# ---------------------------------------------------------------------------

DEFAULT_EDITORIAL_POLICY = """You are the edition editor for an executive cyber security morning briefing.
Your goal is to synthesize incoming security articles into a high-signal, deeply factual intelligence briefing.

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
1. STORIES: First, cluster the incoming articles into deduplicated stories. For each story, provide a 3-5 sentence substantive summary, 2-4 key fact bullets (CVEs, CVSS, software versions, actor names), and a grounded 'why it matters'. Reference the source article IDs in `article_ids`.
2. TOPICS: Next, identify 2 to 6 cross-cutting thematic topics that group related stories (e.g. 'Edge Perimeter Appliances Under Fire', 'Agentic AI Exploitation and Policy'). For each topic, write a solid synthesis paragraph and reference member story IDs.
3. DEEP DIVES: Select up to {max_deep_dives} stories for deep technical analysis and list their story IDs in `deep_dive_story_ids`.
   CRITICAL PRIORITY RULE FOR DEEP DIVES:
   You must select deep dives ONLY from these areas of direct interest, prioritizing strictly in this order:
     Priority 1: Mobile Security (`mobile_security`)
     Priority 2: Security UX & Modern Auth (`security_ux` or `end_user_security` - user experience, passkeys, WebAuthn, authentication friction)
     Priority 3: Corporate Security & Internal Threat (`corporate_security` - enterprise network, VPN, edge gateways, insider threat)
     Priority 4: Account Takeover (`account_takeover` - credential stuffing, session hijacking, identity compromise)
   Do NOT select deep dives from any other areas unless zero stories exist in the four priority areas above.
4. EXECUTIVE SUMMARY: Finally, synthesize the entire edition into:
   - `bottom_line`: Exactly 1 powerful sentence capturing the overarching reality of today's threat landscape.
   - `key_points`: 3 to {max_exec_points} prioritized bullet points (1-2 sentences each), linking to their relevant `story_ids`.
"""

DEEP_DIVE_PROMPT = """You are a senior cyber security technical analyst performing a deep dive on a critical security development.
Analyze the provided source texts for this story and produce an exhaustive, highly technical report strictly following the JSON schema.

Non-Negotiable Rules:
1. STRICT GROUNDING: Use only facts, technical mechanisms, CVEs, version numbers, and actor names explicitly cited in the source texts. Never extrapolate.
2. BAN INTERNAL-ENVIRONMENT CLAIMS: Do not claim or imply the reader's organization uses the affected product.
3. PRESERVE UNCERTAINTY: State clearly what is known vs. unknown (e.g. unconfirmed attribution, missing patch timeline, whether PoC exists).
4. TECHNICAL DEPTH: Provide detailed root cause, exploit mechanism, C2 behavior, and protocols whenever present in the sources.
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

    # Resolve deep dive picks
    raw_dd_ids = editor_data.get("deep_dive_story_ids") or []
    valid_dd_ids = [
        str(sid).strip() for sid in raw_dd_ids if str(sid).strip() in stories_by_id
    ]

    return {
        "stories": resolved_stories,
        "topics": resolved_topics,
        "deep_dive_story_ids": valid_dd_ids,
        "executive_summary": resolved_exec,
    }


def generate_single_deep_dive(
    story: Dict[str, Any],
    article_lookup: Dict[str, dict],
    model: Optional[str] = None,
    timeout: float = 120.0,
    reasoning_effort: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Synthesize a single deep dive from the full text of its source articles."""
    story_id = story.get("id", "unknown")
    art_ids = story.get("article_ids") or []
    source_articles = [article_lookup[aid] for aid in art_ids if aid in article_lookup]

    if not source_articles:
        logger.warning("No source articles found for deep dive on story %s", story_id)
        return None

    user_payload = {
        "story_title": story.get("title"),
        "story_summary": story.get("summary"),
        "primary_area": story.get("primary_area"),
        "key_facts": story.get("key_facts"),
        "articles": [
            {
                "id": a.get("id"),
                "title": a.get("title"),
                "url": a.get("url"),
                "summary": a.get("summary"),
                "content": a.get("text") or a.get("summary") or "",
            }
            for a in source_articles
        ],
    }

    messages = [
        {"role": "system", "content": DEEP_DIVE_PROMPT},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, indent=2),
        },
    ]

    reasoning_payload = {"effort": reasoning_effort} if reasoning_effort else None
    try:
        response = dict(
            complete_structured(
                messages=messages,
                json_schema=DEEP_DIVE_SCHEMA,
                schema_name="story_deep_dive",
                model=model,
                timeout=timeout,
                reasoning=reasoning_payload,
            )
        )
        response["story_id"] = story_id
        response["title"] = story.get("title")
        response["primary_area"] = story.get("primary_area")
        response["source_urls"] = story.get("source_urls") or []
        return response
    except Exception:
        logger.exception("Failed to generate deep dive for story %s", story_id)
        return None


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
) -> Dict[str, Any]:
    """Generate a pyramid editorial digest edition using OpenRouter.

    Two-stage architecture:
      Stage 1: Edition editor call (clustering, topics, exec summary, deep dive picks)
      Stage 2: Parallel deep-dive calls for the top selected stories.
    """
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
            "deep_dives": [],
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

    # Compose the Stage 1 system prompt:
    # 1. Editorial policy (user custom or default)
    # 2. Structural instructions (always included)
    # 3. Focus area definitions (always included if configured)
    policy_prompt = (system_prompt or DEFAULT_EDITORIAL_POLICY).strip()
    structural_prompt = STRUCTURE_INSTRUCTIONS.format(
        max_deep_dives=cfg.deep_dives,
        max_exec_points=cfg.exec_summary_points,
    ).strip()

    prompt_parts = [policy_prompt, structural_prompt]
    if areas:
        areas_desc = "\n".join(f"   - {k}: {v.description}" for k, v in areas.items())
        prompt_parts.append(
            f"PRIMARY AREAS: Assign each story's primary_area strictly to one of these configured areas:\n{areas_desc}"
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
            "summary": "Dry run summary of incoming security articles.",
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
            "deep_dives": [],
            "deep_dive_story_ids": [],
            # Backward-compat aliases
            "overview": f"Dry run overview for {len(prepared_articles)} articles.",
            "attention": [sample_story],
            "watch": [],
        }

    logger.info(
        "Calling Stage 1 OpenRouter editor for %d articles",
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
    stories_by_id = {s["id"]: s for s in resolved["stories"]}

    # Stage 2: Deep dives in parallel.
    # Select only stories in direct areas of interest in strict priority order:
    # 1. mobile_security
    # 2. security_ux / end_user_security (Security UX & Modern Auth)
    # 3. corporate_security (Corporate Security & Internal Threat)
    # 4. account_takeover
    priority_order = [
        "mobile_security",
        "security_ux",
        "end_user_security",
        "corporate_security",
        "account_takeover",
    ]
    tier_rank = {"critical": 0, "important": 1, "notable": 2}

    def story_priority_key(story: dict) -> tuple:
        area = story.get("primary_area") or ""
        try:
            area_idx = priority_order.index(area)
        except ValueError:
            area_idx = 99
        tier_idx = tier_rank.get(story.get("tier"), 1)
        return (area_idx, tier_idx)

    # First, collect candidate stories nominated by editor that match priority areas
    nominated_sids = resolved["deep_dive_story_ids"]
    nominated_stories = [
        stories_by_id[sid] for sid in nominated_sids if sid in stories_by_id
    ]

    # Filter to only priority areas
    priority_candidates = [
        s for s in nominated_stories if s.get("primary_area") in priority_order
    ]

    # If the editor didn't nominate enough priority stories, supplement from all stories
    if len(priority_candidates) < cfg.deep_dives:
        nominated_set = {s["id"] for s in priority_candidates}
        remaining_priority = [
            s
            for s in resolved["stories"]
            if s.get("primary_area") in priority_order and s["id"] not in nominated_set
        ]
        remaining_priority.sort(key=story_priority_key)
        priority_candidates.extend(remaining_priority)

    # Sort candidates strictly by priority area order, then tier
    priority_candidates.sort(key=story_priority_key)

    # Final deep dive story IDs to process
    deep_dive_sids = [s["id"] for s in priority_candidates[: cfg.deep_dives]]
    resolved["deep_dive_story_ids"] = deep_dive_sids
    deep_dives: List[Dict[str, Any]] = []

    if deep_dive_sids:
        dd_model = cfg.deep_dive_model or model
        logger.info(
            "Calling Stage 2 deep dives for %d stories using model=%s",
            len(deep_dive_sids),
            dd_model,
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(deep_dive_sids)
        ) as executor:
            future_to_sid = {
                executor.submit(
                    generate_single_deep_dive,
                    stories_by_id[sid],
                    article_lookup,
                    model=dd_model,
                    reasoning_effort=reasoning_effort,
                ): sid
                for sid in deep_dive_sids
                if sid in stories_by_id
            }
            for future in concurrent.futures.as_completed(future_to_sid):
                sid = future_to_sid[future]
                try:
                    dd = future.result()
                    if dd:
                        deep_dives.append(dd)
                except Exception:
                    logger.exception(
                        "Unhandled exception in deep dive future for %s", sid
                    )

        # Preserve the editor's preference order
        order_map = {sid: i for i, sid in enumerate(deep_dive_sids)}
        deep_dives.sort(key=lambda d: order_map.get(d.get("story_id"), 999))

    # Backward compatibility projections (overview, attention, watch)
    overview_text = resolved["executive_summary"].get("bottom_line") or ""
    attention_list = [s for s in resolved["stories"] if s.get("tier") == "critical"]
    watch_list = [s for s in resolved["stories"] if s.get("tier") != "critical"]

    logger.info(
        "Generated pyramid edition: %d stories, %d topics, %d deep dives",
        len(resolved["stories"]),
        len(resolved["topics"]),
        len(deep_dives),
    )

    return {
        "executive_summary": resolved["executive_summary"],
        "topics": resolved["topics"],
        "stories": resolved["stories"],
        "deep_dives": deep_dives,
        "deep_dive_story_ids": resolved["deep_dive_story_ids"],
        # Backward compatibility fields
        "overview": overview_text,
        "attention": attention_list,
        "watch": watch_list,
    }
