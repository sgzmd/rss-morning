"""Rendering helpers for email outputs."""

from __future__ import annotations

from typing import Any

from .templating import get_environment


def build_email_html(
    payload: Any, is_summary: bool, fallback: str | None = None
) -> str:
    """Render the HTML email body using the Jinja2 template."""
    env = get_environment()
    template = env.get_template("email.html.j2")
    return template.render(payload=payload, is_summary=is_summary, fallback=fallback)


def build_email_text(
    payload: Any, is_summary: bool, fallback: str | None = None
) -> str:
    """Render the plain-text email body using the Jinja2 template."""
    env = get_environment()
    template = env.get_template("email.txt.j2")
    return template.render(payload=payload, is_summary=is_summary, fallback=fallback)
