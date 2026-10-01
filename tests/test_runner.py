import requests.adapters  # noqa: F401

import json
from datetime import datetime, timezone, timedelta

import pytest

from rss_morning.articles import ArticleContent
from rss_morning.classify import ClassificationDecision
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


@pytest.fixture(autouse=True)
def mock_classify(monkeypatch):
    monkeypatch.setattr(
        runner,
        "classify_entry",
        lambda entry_meta, **kwargs: ClassificationDecision(
            relevance_probability=1.0,
            primary_area="other",
            area_confidence=1.0,
            is_plausible=True,
        ),
    )


def test_execute_standard_flow(monkeypatch):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(
            text="article text", image="https://img.example.com"
        ),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")
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
    assert payload[0]["image"] == "https://img.example.com"
    assert not result.is_summary


def test_execute_summary_flow(monkeypatch):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(
            text="body", image="https://example.com/img.jpg"
        ),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)

    edition_payload = {
        "overview": "Overview of today's events.",
        "attention": [
            {
                "title": "Critical Bug",
                "summary": "Actively exploited vulnerability.",
                "source_urls": ["https://example.com"],
                "primary_area": "corporate_it",
                "urgency_rationale": "In-the-wild exploitation.",
            }
        ],
        "watch": [],
    }

    def fake_generate(articles, system_prompt=None, dry_run=False, model=None):
        return edition_payload

    monkeypatch.setattr(runner, "generate_digest", fake_generate)
    calls = []
    monkeypatch.setattr(
        runner, "send_email_report", lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(
        runner,
        "_build_default_email_subject",
        lambda: "RSS Mailer update for 1999-12-31 at 23:59",
    )

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=True,
        email_to="user@example.com",
        email_from=None,
        email_subject=None,
        system_prompt="You are a secure AI",
    )

    result = execute(config)

    rendered = json.loads(result.output_text)
    assert rendered["overview"] == "Overview of today's events."
    assert len(rendered["attention"]) == 1
    assert rendered["attention"][0]["title"] == "Critical Bug"
    assert result.is_summary
    assert calls[0]["is_summary"] is True
    assert calls[0]["subject"] == "RSS Mailer update for 1999-12-31 at 23:59"
    assert calls[0]["payload"] == edition_payload


def test_execute_uses_custom_email_subject(monkeypatch):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")

    captured = {}

    def fake_send_email(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(runner, "send_email_report", fake_send_email)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        email_to="user@example.com",
        email_from=None,
        email_subject="Custom Subject",
    )

    execute(config)

    assert captured["subject"] == "Custom Subject"


def test_execute_classification_applies_when_enabled(monkeypatch):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    classified = []

    def fake_classify(entry_meta, *, model, threshold):
        classified.append((entry_meta, model, threshold))
        return ClassificationDecision(
            relevance_probability=0.85,
            primary_area="vulnerability",
            area_confidence=0.9,
            is_plausible=True,
        )

    monkeypatch.setattr(runner, "classify_entry", fake_classify)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        classify=True,
        classify_model="jev-latest",
        classify_threshold=0.50,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    result = execute(config)
    payload = json.loads(result.output_text)

    assert len(classified) == 1
    assert classified[0][0]["link"] == "https://example.com"
    assert classified[0][1] == "jev-latest"
    assert classified[0][2] == 0.50
    assert len(payload) == 1
    assert payload[0]["url"] == "https://example.com"


def test_execute_classification_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    def fail_classify(*args, **kwargs):
        raise AssertionError("classify_entry should not be called when classify=False")

    monkeypatch.setattr(runner, "classify_entry", fail_classify)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        classify=False,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    result = execute(config)
    payload = json.loads(result.output_text)

    assert len(payload) == 1
    assert payload[0]["url"] == "https://example.com"


def test_execute_load_articles_short_circuits_fetch(monkeypatch, tmp_path):
    snapshot = tmp_path / "articles.json"
    payload = [{"url": "https://loaded.example.com", "title": "Loaded"}]
    snapshot.write_text(json.dumps(payload))

    def fail_collect(_):
        raise AssertionError("_collect_entries should not run when loading from file")

    monkeypatch.setattr(runner, "_collect_entries", fail_collect)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        classify=False,
        save_articles_path=None,
        load_articles_path=str(snapshot),
    )

    result = execute(config)
    data = json.loads(result.output_text)

    assert data == payload


def test_execute_save_articles_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda feed: [_feed_entry("https://example.com")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    save_path = tmp_path / "fetched.json"

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        classify=False,
        save_articles_path=str(save_path),
        load_articles_path=None,
    )

    result = execute(config)
    saved = json.loads(save_path.read_text())

    assert saved == json.loads(result.output_text)
    assert saved[0]["text"] == "trimmed"


def test_execute_limit_applies_per_feed(monkeypatch):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)
    feeds = [
        FeedConfig("Cat1", "Feed 1", "feed-1"),
        FeedConfig("Cat2", "Feed 2", "feed-2"),
    ]
    monkeypatch.setattr(runner, "parse_feeds_config", lambda path: feeds)

    feed_entries = {
        "feed-1": [
            FeedEntry(
                link="https://example.com/feed1-new",
                category="Cat1",
                title="Latest 1",
                published=now,
                summary="Summary 1",
            ),
            FeedEntry(
                link="https://example.com/feed1-old",
                category="Cat1",
                title="Older 1",
                published=now - timedelta(days=1),
                summary="Summary 1 old",
            ),
        ],
        "feed-2": [
            FeedEntry(
                link="https://example.com/feed2-new",
                category="Cat2",
                title="Latest 2",
                published=now - timedelta(hours=1),
                summary="Summary 2",
            ),
            FeedEntry(
                link="https://example.com/feed2-old",
                category="Cat2",
                title="Older 2",
                published=now - timedelta(days=3),
                summary="Summary 2 old",
            ),
        ],
    }

    def fake_fetch(feed):
        return list(feed_entries[feed.url])

    monkeypatch.setattr(runner, "fetch_feed_entries", fake_fetch)
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text=None, image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    calls = []
    original_select = runner.select_recent_entries

    def recording_select(entries, limit, cutoff):
        calls.append({"links": [entry.link for entry in entries], "limit": limit})
        return original_select(entries, limit, cutoff)

    monkeypatch.setattr(runner, "select_recent_entries", recording_select)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=1,
        max_age_hours=None,
        summary=False,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    result = execute(config)
    payload = json.loads(result.output_text)

    assert len(payload) == 2
    assert {item["url"] for item in payload} == {
        "https://example.com/feed1-new",
        "https://example.com/feed2-new",
    }
    assert len(calls) == 2
    assert all(call["limit"] == 1 for call in calls)


def test_execute_validates_max_age(monkeypatch):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
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
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )

    def fake_fetch(feed):
        return []

    monkeypatch.setattr(runner, "fetch_feed_entries", fake_fetch)
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)
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
