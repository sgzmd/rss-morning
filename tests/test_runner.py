import json
from datetime import datetime, timezone

import pytest

from rss_morning.models import FeedConfig, FeedEntry
from rss_morning.runner import RunConfig, execute
import rss_morning.runner as runner


def _feed_entry(link: str) -> FeedEntry:
    return FeedEntry(
        link=link,
        category="Cat",
        title="Title",
        published=datetime(2024, 1, 1, tzinfo=timezone.utc),
        summary="Summary",
    )


def test_execute_standard_flow(monkeypatch):
    monkeypatch.setattr(runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")])
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")])
    monkeypatch.setattr(runner, "select_recent_entries", lambda entries, limit, cutoff: entries)
    monkeypatch.setattr(runner, "fetch_article_text", lambda url: "article text")
    monkeypatch.setattr(runner, "truncate_text", lambda text: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    result = execute(config)
    payload = json.loads(result.output_text)

    assert payload[0]["text"] == "trimmed"
    assert not result.is_summary


def test_execute_summary_flow(monkeypatch):
    monkeypatch.setattr(runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")])
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")])
    monkeypatch.setattr(runner, "select_recent_entries", lambda entries, limit, cutoff: entries)
    monkeypatch.setattr(runner, "fetch_article_text", lambda url: None)
    monkeypatch.setattr(runner, "truncate_text", lambda text: text)

    summary_payload = {"summaries": [{"url": "https://example.com"}]}

    def fake_generate(articles, return_dict):
        return json.dumps(summary_payload), summary_payload

    monkeypatch.setattr(runner, "generate_summary", fake_generate)
    calls = []
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: calls.append(kwargs))

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=True,
        email_to="user@example.com",
        email_from=None,
        email_subject=None,
    )

    result = execute(config)

    assert json.loads(result.output_text)["summaries"]
    assert result.is_summary
    assert calls[0]["is_summary"] is True


def test_execute_validates_max_age(monkeypatch):
    monkeypatch.setattr(runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")])
    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=0,
        summary=False,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    with pytest.raises(ValueError):
        execute(config)


def test_execute_raises_when_no_entries(monkeypatch):
    monkeypatch.setattr(runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")])

    def fake_fetch(feed):
        return []

    monkeypatch.setattr(runner, "fetch_feed_entries", fake_fetch)
    monkeypatch.setattr(runner, "select_recent_entries", lambda entries, limit, cutoff: entries)
    monkeypatch.setattr(runner, "fetch_article_text", lambda url: "text")
    monkeypatch.setattr(runner, "truncate_text", lambda text: text)
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    with pytest.raises(RuntimeError):
        execute(config)
