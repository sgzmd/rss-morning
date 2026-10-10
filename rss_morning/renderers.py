"""Rendering helpers for email outputs (Pyramid Principle: Exec Summary, Topics, Deep Dives)."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Mapping, Optional

from .models import AreaConfig
from .templating import get_environment


def prepare_pyramid_view_model(
    payload: Any,
    areas: Optional[Mapping[str, AreaConfig]] = None,
) -> Dict[str, Any]:
    """Prepare a structured view model for pyramid rendering."""
    if not isinstance(payload, dict):
        return {}

    # Only treat as pyramid format if stories, topics, or executive_summary is explicitly present
    if not (
        payload.get("stories")
        or payload.get("topics")
        or payload.get("executive_summary")
    ):
        return {}

    stories_list = list(payload.get("stories") or [])
    story_map: Dict[str, Dict[str, Any]] = {
        s.get("id"): s for s in stories_list if isinstance(s, dict) and s.get("id")
    }

    # Resolve executive summary
    raw_exec = payload.get("executive_summary") or {}
    bottom_line = str(
        raw_exec.get("bottom_line") or payload.get("overview") or ""
    ).strip()
    exec_points = []
    for pt in raw_exec.get("key_points") or []:
        if isinstance(pt, dict):
            sids = [sid for sid in (pt.get("story_ids") or []) if sid in story_map]
            exec_points.append(
                {
                    "text": str(pt.get("text") or "").strip(),
                    "story_ids": sids,
                    "stories": [story_map[sid] for sid in sids],
                }
            )

    # Resolve topics
    topics_list = list(payload.get("topics") or [])
    resolved_topics: List[Dict[str, Any]] = []
    stories_in_topics = set()
    for topic in topics_list:
        if not isinstance(topic, dict):
            continue
        sids = [sid for sid in (topic.get("story_ids") or []) if sid in story_map]
        stories_in_topics.update(sids)
        resolved_topics.append(
            {
                "id": topic.get("id"),
                "title": topic.get("title"),
                "synthesis": topic.get("synthesis"),
                "story_ids": sids,
                "stories": [story_map[sid] for sid in sids],
            }
        )

    # Standalone stories not assigned to any topic
    leftover_stories = [s for s in stories_list if s.get("id") not in stories_in_topics]

    # Group leftover stories by area if areas are provided
    configured_areas = dict(areas or {})
    grouped_leftovers: Dict[str, List[Dict[str, Any]]] = {}
    for story in leftover_stories:
        area_key = story.get("primary_area") or "other"
        grouped_leftovers.setdefault(area_key, []).append(story)

    leftover_sections: List[Dict[str, Any]] = []
    seen_area_keys = set()
    for key, area_cfg in configured_areas.items():
        seen_area_keys.add(key)
        items = grouped_leftovers.get(key, [])
        if items:
            label = getattr(area_cfg, "label", key.replace("_", " ").title())
            leftover_sections.append({"key": key, "label": label, "stories": items})

    for key, items in grouped_leftovers.items():
        if key not in seen_area_keys and items:
            leftover_sections.append(
                {"key": key, "label": key.replace("_", " ").title(), "stories": items}
            )

    return {
        "bottom_line": bottom_line,
        "key_points": exec_points,
        "topics": resolved_topics,
        "leftover_stories": leftover_stories,
        "leftover_sections": leftover_sections,
        "deep_dives": [],
        "story_map": story_map,
    }


def prepare_sections_by_area(
    payload: Any,
    areas: Optional[Mapping[str, AreaConfig]] = None,
) -> List[Dict[str, Any]]:
    """Backward compatibility helper: groups attention and watch by area."""
    if not isinstance(payload, dict) or not areas:
        return []

    configured_areas = dict(areas or {})
    attention_items = payload.get("attention") or []
    watch_items = payload.get("watch") or []

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in attention_items:
        area_key = item.get("primary_area") or "other"
        grouped.setdefault(area_key, []).append({**item, "is_attention": True})

    for item in watch_items:
        area_key = item.get("primary_area") or "other"
        grouped.setdefault(area_key, []).append({**item, "is_attention": False})

    sections: List[Dict[str, Any]] = []
    seen_keys = set()

    for key, area_cfg in configured_areas.items():
        seen_keys.add(key)
        items = grouped.get(key, [])
        if items:
            label = getattr(area_cfg, "label", key.replace("_", " ").title())
            sections.append(
                {
                    "key": key,
                    "label": label,
                    "items": items,
                    "articles": items,
                }
            )

    for key, items in grouped.items():
        if key not in seen_keys and items:
            sections.append(
                {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "items": items,
                    "articles": items,
                }
            )

    return sections


def build_email_html(
    payload: Any,
    is_summary: bool,
    fallback: str | None = None,
    areas: Optional[Mapping[str, AreaConfig]] = None,
    profile: str = "security",
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    date_str: Optional[str] = None,
) -> str:
    """Render the HTML email body using the Jinja2 template."""
    env = get_environment()
    template = env.get_template("email.html.j2")
    today = date_str or datetime.date.today().strftime("%B %d, %Y")
    pyramid = prepare_pyramid_view_model(payload, areas) if is_summary else {}
    sections = prepare_sections_by_area(payload, areas) if is_summary else []
    return template.render(
        payload=payload,
        is_summary=is_summary,
        fallback=fallback,
        date=today,
        sections=sections,
        pyramid=pyramid,
        profile=profile,
        title=title,
        subtitle=subtitle,
    )


def build_email_text(
    payload: Any,
    is_summary: bool,
    fallback: str | None = None,
    areas: Optional[Mapping[str, AreaConfig]] = None,
    profile: str = "security",
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    date_str: Optional[str] = None,
) -> str:
    """Render the plain-text email body using the Jinja2 template."""
    env = get_environment()
    template = env.get_template("email.txt.j2")
    today = date_str or datetime.date.today().strftime("%B %d, %Y")
    pyramid = prepare_pyramid_view_model(payload, areas) if is_summary else {}
    sections = prepare_sections_by_area(payload, areas) if is_summary else []
    return template.render(
        payload=payload,
        is_summary=is_summary,
        fallback=fallback,
        date=today,
        sections=sections,
        pyramid=pyramid,
        profile=profile,
        title=title,
        subtitle=subtitle,
    )
