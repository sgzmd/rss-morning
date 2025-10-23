"""Rendering helpers for email outputs."""

from __future__ import annotations

from html import escape
from typing import Any


def to_html_paragraph(text: str) -> str:
    if not text:
        return ""
    return "<br>".join(escape(text).splitlines())


def build_email_html(payload: Any, is_summary: bool) -> str:
    style = """
    <style>
      body { font-family: Roboto, Google Sans, sans-serif; background-color: #f5f5f5; color: #1a1a1a; margin: 0; padding: 24px; }
      .container { max-width: 720px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08); }
      header { background-color: #1f2937; color: #ffffff; padding: 24px; }
      header h1 { margin: 0; font-size: 24px; }
      .content { padding: 24px; }
      .card { border-bottom: 1px solid #e5e7eb; padding: 16px 0; }
      .card:last-child { border-bottom: none; }
      .badge { display: inline-block; background-color: #2563eb; color: #ffffff; padding: 4px 8px; border-radius: 999px; font-size: 12px; margin-bottom: 12px; }
      h2 { font-size: 20px; margin: 0 0 12px 0; }
      p { margin: 8px 0; line-height: 1.6; }
      a { color: #2563eb; text-decoration: none; }
      footer { padding: 16px 24px; font-size: 12px; color: #6b7280; background-color: #f3f4f6; }
      .section-label { font-weight: bold; color: #374151; text-transform: uppercase; font-size: 12px; letter-spacing: 0.08em; margin-bottom: 6px; }
    </style>
    """

    cards: list[str] = []

    if is_summary and isinstance(payload, dict):
        summaries = payload.get("summaries") or []
        if not summaries:
            cards.append("<p>No relevant articles identified.</p>")
        for index, item in enumerate(summaries, start=1):
            url = item.get("url", "")
            summary = item.get("summary") or {}
            what = to_html_paragraph(summary.get("what", ""))
            so_what = to_html_paragraph(summary.get("so-what", ""))
            now_what = to_html_paragraph(summary.get("now-what", ""))
            link_html = f'<a href="{escape(url)}" target="_blank" rel="noopener">View Article</a>' if url else ""
            cards.append(
                f"""
                <div class="card">
                  <div class="badge">Insight {index}</div>
                  <h2>{escape(summary.get("title", f"Update {index}"))}</h2>
                  <div class="section-label">What</div>
                  <p>{what or "—"}</p>
                  <div class="section-label">So What</div>
                  <p>{so_what or "—"}</p>
                  <div class="section-label">Now What</div>
                  <p>{now_what or "—"}</p>
                  <p>{link_html}</p>
                </div>
                """
            )
    elif isinstance(payload, list):
        if not payload:
            cards.append("<p>No articles retrieved.</p>")
        for article in payload:
            title = article.get("title") or article.get("url") or "Untitled Article"
            category = article.get("category")
            summary = to_html_paragraph(article.get("summary", ""))
            text = to_html_paragraph(article.get("text", ""))
            url = article.get("url")
            link_html = f'<a href="{escape(url)}" target="_blank" rel="noopener">Read Full Article</a>' if url else ""
            category_badge = f'<div class="badge">{escape(category)}</div>' if category else ""
            cards.append(
                f"""
                <div class="card">
                  {category_badge}
                  <h2>{escape(title)}</h2>
                  <div class="section-label">Summary</div>
                  <p>{summary or "—"}</p>
                  {"<div class='section-label'>Excerpt</div><p>" + text + "</p>" if text else ""}
                  <p>{link_html}</p>
                </div>
                """
            )
    else:
        cards.append(f"<pre>{escape(str(payload))}</pre>")

    body = "\n".join(cards)
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <meta charset="UTF-8" />
        <title>RSS Morning Briefing</title>
        {style}
      </head>
      <body>
        <div class="container">
          <header>
            <h1>RSS Morning Briefing</h1>
          </header>
          <div class="content">
            {body}
          </div>
          <footer>
            Generated automatically by rss-morning.
          </footer>
        </div>
      </body>
    </html>
    """
    return html_content


def build_email_text(payload: Any, is_summary: bool) -> str:
    lines: list[str] = []
    if is_summary and isinstance(payload, dict):
        summaries = payload.get("summaries") or []
        if not summaries:
            lines.append("No relevant articles identified.")
        for item in summaries:
            url = item.get("url", "")
            summary = item.get("summary") or {}
            what = summary.get("what", "").strip()
            so_what = summary.get("so-what", "").strip()
            now_what = summary.get("now-what", "").strip()
            title = summary.get("title") or url or "Update"
            section = [
                f"Title: {title}",
                f"What: {what}" if what else "",
                f"So What: {so_what}" if so_what else "",
                f"Now What: {now_what}" if now_what else "",
                f"Link: {url}" if url else "",
            ]
            lines.append("\n".join(filter(None, section)))
    elif isinstance(payload, list):
        if not payload:
            lines.append("No articles retrieved.")
        for article in payload:
            title = article.get("title") or article.get("url") or "Untitled Article"
            summary = (article.get("summary") or "").strip()
            text = (article.get("text") or "").strip()
            url = article.get("url") or ""
            section = [
                f"Title: {title}",
                f"Summary: {summary}" if summary else "",
                f"Excerpt: {text}" if text else "",
                f"Link: {url}" if url else "",
            ]
            lines.append("\n".join(filter(None, section)))
    else:
        lines.append(str(payload))

    return "\n\n".join(lines)
