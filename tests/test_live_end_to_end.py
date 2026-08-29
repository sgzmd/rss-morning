"""Opt-in, networked validation of the complete user-facing pipeline."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

import pytest

from rss_morning import cli, emailing

pytestmark = pytest.mark.live_e2e


def _require_live_test() -> None:
    if os.environ.get("RUN_LIVE_E2E") != "1":
        pytest.skip("set RUN_LIVE_E2E=1 to run the live end-to-end test")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.fail("OPENROUTER_API_KEY is required when RUN_LIVE_E2E=1")


def test_live_cli_pipeline_fakes_only_email_delivery(monkeypatch, tmp_path):
    """Exercise live inputs and models, then inspect the rendered email boundary."""
    _require_live_test()

    feeds_path = tmp_path / "feeds.opml"
    feeds_path.write_text(
        """\
<opml version="2.0">
  <body>
    <outline text="Technology">
      <outline type="rss" text="Python Blog" xmlUrl="https://blog.python.org/feeds/posts/default?alt=rss" />
      <outline type="rss" text="Cloudflare Blog" xmlUrl="https://blog.cloudflare.com/rss/" />
      <outline type="rss" text="Schneier on Security" xmlUrl="https://www.schneier.com/feed/atom/" />
    </outline>
  </body>
</opml>
""",
        encoding="utf-8",
    )

    queries_path = tmp_path / "queries.json"
    queries_path.write_text(
        json.dumps(
            {
                "End-to-end relevant news": [
                    "technology software programming internet cybersecurity news"
                ]
            }
        ),
        encoding="utf-8",
    )

    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text(
        """\
Summarize every supplied article. Do not omit any article. Preserve each input URL
and category exactly.
Return a summaries array. For every summary include URL, category, and a nested
summary with title, rank-reasoning, what, so-what, and now-what. Keep every field
concise and use plain text.
""",
        encoding="utf-8",
    )

    database_path = tmp_path / "live-e2e.sqlite3"
    config_path = tmp_path / "config.xml"
    config_path.write_text(
        f"""\
<config>
  <feeds>{feeds_path.name}</feeds>
  <limit>1</limit>
  <summary>true</summary>
  <max-article-length>180</max-article-length>
  <extractor>trafilatura</extractor>
  <concurrency>3</concurrency>
  <prompt file="{prompt_path.name}" />
  <pre-filter>
    <enabled>true</enabled>
    <queries-file>{queries_path.name}</queries-file>
  </pre-filter>
  <embeddings>
    <provider>fastembed</provider>
    <model>BAAI/bge-small-en-v1.5</model>
  </embeddings>
  <llm>
    <provider>openrouter</provider>
    <model>{os.environ.get("OPENROUTER_E2E_MODEL", "bytedance-seed/seed-2.0-mini")}</model>
  </llm>
  <database>
    <enabled>true</enabled>
    <connection-string>sqlite:///{database_path}</connection-string>
  </database>
  <email>
    <to>e2e-recipient@example.com</to>
    <from>e2e-sender@example.com</from>
    <subject>RSS Morning live end-to-end test</subject>
  </email>
</config>
""",
        encoding="utf-8",
    )

    sent_messages = []

    def fake_resend_send(message):
        sent_messages.append(message)
        return SimpleNamespace(id="e2e-fake-delivery")

    assert emailing.resend is not None, "the Resend dependency must be installed"
    monkeypatch.setattr(emailing.resend.Emails, "send", fake_resend_send)
    monkeypatch.setenv("RESEND_API_KEY", "e2e-placeholder-key")

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = cli.main(["--config", str(config_path), "--log-level", "DEBUG"])
    assert exit_code == 0

    result = json.loads(stdout.getvalue())
    summaries = result.get("summaries")
    assert isinstance(summaries, list) and summaries
    for item in summaries:
        assert item["url"].startswith("http")
        assert item["category"] == "End-to-end relevant news"
        assert set(item["summary"]) >= {
            "title",
            "rank-reasoning",
            "what",
            "so-what",
            "now-what",
        }

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message["to"] == ["e2e-recipient@example.com"]
    assert message["subject"] == "RSS Morning live end-to-end test"
    assert "<html" in message["html"].lower()
    assert summaries[0]["summary"]["title"] in message["text"]

    with sqlite3.connect(database_path) as connection:
        article_count = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[
            0
        ]
        extracted_article_count = connection.execute(
            "SELECT COUNT(*) FROM articles WHERE content IS NOT NULL AND length(content) > 0"
        ).fetchone()[0]
        embedding_count = connection.execute(
            "SELECT COUNT(*) FROM embeddings_v2"
        ).fetchone()[0]
    assert article_count >= 1
    assert extracted_article_count >= 1
    assert embedding_count >= 1
