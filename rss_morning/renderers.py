"""Rendering helpers for email outputs."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Mapping, Optional

from .models import AreaConfig
from .templating import get_environment


def prepare_sections_by_area(
    payload: Any,
    areas: Optional[Mapping[str, AreaConfig]] = None,
) -> List[Dict[str, Any]]:
    """Group attention and watch stories under their respective primary areas."""
    if not isinstance(payload, dict) or not areas:
        return []

    configured_areas = dict(areas or {})
    attention_items = payload.get("attention") or []
    watch_items = payload.get("watch") or []

    # Map area key -> list of items (preserving urgency)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in attention_items:
        area_key = item.get("primary_area") or "other"
        grouped.setdefault(area_key, []).append({**item, "is_attention": True})

    for item in watch_items:
        area_key = item.get("primary_area") or "other"
        grouped.setdefault(area_key, []).append({**item, "is_attention": False})

    sections: List[Dict[str, Any]] = []
    seen_keys = set()

    # Process in the order of configured_areas
    for key, area_cfg in configured_areas.items():
        seen_keys.add(key)
        items = grouped.get(key, [])
        if items:
            label = (
                area_cfg.label
                if hasattr(area_cfg, "label")
                else key.replace("_", " ").title()
            )
            sections.append(
                {
                    "key": key,
                    "label": label,
                    "items": items,
                    "articles": items,
                }
            )

    # Process any remaining areas not in configured_areas
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
) -> str:
    """Render the HTML email body using the Jinja2 template."""
    env = get_environment()
    template = env.get_template("email.html.j2")
    today = datetime.date.today().strftime("%B %d, %Y")
    sections = prepare_sections_by_area(payload, areas) if is_summary else []
    return template.render(
        payload=payload,
        is_summary=is_summary,
        fallback=fallback,
        date=today,
        sections=sections,
    )


def build_email_text(
    payload: Any,
    is_summary: bool,
    fallback: str | None = None,
    areas: Optional[Mapping[str, AreaConfig]] = None,
) -> str:
    """Render the plain-text email body using the Jinja2 template."""
    env = get_environment()
    template = env.get_template("email.txt.j2")
    sections = prepare_sections_by_area(payload, areas) if is_summary else []
    return template.render(
        payload=payload,
        is_summary=is_summary,
        fallback=fallback,
        sections=sections,
    )
