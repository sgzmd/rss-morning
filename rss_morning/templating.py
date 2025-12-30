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


def _render_markdown(value: str | None) -> Markup:
    """Render markdown to HTML with sanitization."""
    if not value:
        return Markup("")

    # Import locally to avoiding hard dependency if filter isn't used
    from markdown_it import MarkdownIt
    import bleach

    md = MarkdownIt("commonmark", {"breaks": True, "html": False})
    html = md.render(value)

    # Sanitize allowed tags for email safety
    allowed_tags = ["p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "a"]
    allowed_attrs = {"a": ["href", "title", "target"]}

    clean_html = bleach.clean(
        html, tags=allowed_tags, attributes=allowed_attrs, strip=True
    )

    # Post-process to add inline styles for email client compatibility
    # Simple naive replacement for basic lists and paragraphs
    clean_html = clean_html.replace(
        "<ul>", '<ul style="padding-left: 20px; margin: 0 0 16px 0;">'
    )
    clean_html = clean_html.replace("<li>", '<li style="margin-bottom: 4px;">')
    clean_html = clean_html.replace("<p>", '<p style="margin: 0 0 8px 0;">')

    return Markup(clean_html)


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
        _ENV.filters["markdown"] = _render_markdown
    return _ENV
