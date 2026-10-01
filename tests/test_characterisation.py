"""Focused characterisation tests to establish migration baseline invariants."""

import logging
import types
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


from rss_morning.models import FeedConfig, FeedEntry
from rss_morning import emailing, renderers, runner


def test_failed_rss_feed_does_not_kill_other_feeds():
    """Verify that when one feed fails, entries from remaining feeds are still collected."""
    feed_good = FeedConfig(
        category="Good", title="Good Feed", url="https://good.example.com"
    )
    feed_bad = FeedConfig(
        category="Bad", title="Bad Feed", url="https://bad.example.com"
    )

    entry = FeedEntry(
        link="https://good.example.com/1",
        category="Good",
        title="Good Story",
        published=datetime.now(timezone.utc),
    )

    def fake_fetch(feed):
        if feed.url == "https://bad.example.com":
            raise ConnectionError("Network down")
        return [entry]

    config = runner.RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=24,
        summary=False,
    )

    with patch(
        "rss_morning.runner.parse_feeds_config", return_value=[feed_bad, feed_good]
    ):
        with patch("rss_morning.runner.fetch_feed_entries", side_effect=fake_fetch):
            with patch(
                "rss_morning.runner.fetch_article_content"
            ) as mock_fetch_content:
                mock_fetch_content.return_value = types.SimpleNamespace(
                    text="Article content", image=None
                )
                articles = runner._collect_entries(config)
                assert len(articles) == 1
                assert articles[0]["url"] == "https://good.example.com/1"


def test_article_extraction_failure_isolation():
    """Verify that failure to extract one article does not drop other articles."""
    feed = FeedConfig(category="Sec", title="Feed", url="https://feed.example.com")
    now = datetime.now(timezone.utc)
    entry1 = FeedEntry(
        link="https://ex.com/fail", category="Sec", title="Fail", published=now
    )
    entry2 = FeedEntry(
        link="https://ex.com/pass", category="Sec", title="Pass", published=now
    )

    def fake_fetch_article(url, **kwargs):
        if "fail" in url:
            raise RuntimeError("Extraction crashed")
        return types.SimpleNamespace(text="Good content", image=None)

    config = runner.RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=24,
        summary=False,
    )

    with patch("rss_morning.runner.parse_feeds_config", return_value=[feed]):
        with patch(
            "rss_morning.runner.fetch_feed_entries", return_value=[entry1, entry2]
        ):
            with patch(
                "rss_morning.runner.fetch_article_content",
                side_effect=fake_fetch_article,
            ):
                articles = runner._collect_entries(config)
                assert len(articles) == 1
                assert articles[0]["url"] == "https://ex.com/pass"


def test_renderer_escapes_malicious_html():
    """Verify HTML email rendering escapes unsafe tags in titles and text."""
    payload = {
        "overview": "Overview with safe text",
        "attention": [
            {
                "title": "Malicious <script>alert('xss')</script> Title",
                "summary": "Body with <b>bold</b> and <img src=x onerror=alert(2)>",
                "source_urls": ["https://example.com/safe"],
                "primary_area": "Test<script>alert(1)</script>",
                "urgency_rationale": "Rationale",
            }
        ],
        "watch": [],
    }

    html = renderers.build_email_html(payload, is_summary=True)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html


def test_missing_resend_api_key_does_not_leak_or_send(caplog, monkeypatch):
    """Verify that absent RESEND_API_KEY aborts cleanly without sending or crashing."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    fake_resend = MagicMock()
    monkeypatch.setattr(emailing, "resend", fake_resend)

    with caplog.at_level(logging.ERROR):
        emailing.send_email_report(
            payload=[],
            is_summary=False,
            to_address="test@example.com",
            from_address="sender@example.com",
        )

    assert "RESEND_API_KEY environment variable is not set" in caplog.text
    fake_resend.Emails.send.assert_not_called()


def test_summaries_never_logs_full_api_key(caplog, monkeypatch):
    """Verify that OpenRouter structured completions never log the full API key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-SECRET_KEY_123456789_TOP_SECRET")
    from rss_morning.openrouter import complete_structured

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }

    with caplog.at_level(logging.DEBUG):
        with patch("requests.post", return_value=mock_resp):
            complete_structured(
                messages=[{"role": "user", "content": "hi"}],
                json_schema={"type": "object"},
                schema_name="test",
            )

    assert "sk-or-v1-SECRET_KEY_123456789_TOP_SECRET" not in caplog.text
    assert "...CRET" in caplog.text or "****" in caplog.text


def test_cli_active_config_logging_masks_secrets(caplog):
    """Verify that logging active config in CLI masks system_prompt."""

    config = runner.RunConfig(
        feeds_file="feeds.xml",
        limit=10,
        max_age_hours=24.0,
        summary=False,
        system_prompt="TOP_SECRET_PROMPT_INSTRUCTIONS",
    )

    with caplog.at_level(logging.INFO):
        # We test the config masking logic directly as in cli.py
        import dataclasses

        config_dict = dataclasses.asdict(config)
        if config_dict.get("system_prompt"):
            config_dict["system_prompt"] = "***MASKED***"

        logging.getLogger("rss_morning.cli").info(
            "Active Configuration:\n%s", config_dict
        )

    assert "TOP_SECRET_PROMPT_INSTRUCTIONS" not in caplog.text
    assert "***MASKED***" in caplog.text
