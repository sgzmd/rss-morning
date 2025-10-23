"""Jinja2 environment for rss_morning templates."""

from __future__ import annotations

from importlib import resources

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

_ENV: Environment | None = None


def _nl2br(value: str | None) -> Markup:
    """Convert newlines to <br> tags while escaping HTML."""
    if not value:
        return Markup("")
    return Markup("<br>".join(escape(value).splitlines()))


def get_environment() -> Environment:
    """Return a cached Jinja environment configured for package templates."""
    global _ENV
    if _ENV is None:
        template_dir = resources.files(__package__) / "templates"
        loader = FileSystemLoader(str(template_dir))
        _ENV = Environment(
            loader=loader,
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        _ENV.filters["nl2br"] = _nl2br
    return _ENV
