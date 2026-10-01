"""Jinja2 environment for rss_morning templates."""

from __future__ import annotations

from importlib import resources

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from urllib.parse import urlparse

_ENV: Environment | None = None


def _nl2br(value: str | None) -> Markup:
    """Convert newlines to <br> tags while escaping HTML."""
    if not value:
        return Markup("")
    return Markup("<br>".join(escape(value).splitlines()))


def _extract_domain(value: str | None) -> str:
    """Extract domain from URL."""
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return value or ""


def get_environment() -> Environment:
    """Return a cached Jinja environment configured for package templates."""
    global _ENV
    if _ENV is None:
        template_dir = resources.files(__package__) / "templates"
        loader = FileSystemLoader(str(template_dir))
        _ENV = Environment(
            loader=loader,
            autoescape=select_autoescape(["html", "xml", "html.j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        _ENV.filters["nl2br"] = _nl2br
        _ENV.filters["domain"] = _extract_domain
    return _ENV
