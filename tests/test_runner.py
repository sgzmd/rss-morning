import requests.adapters  # noqa: F401

import json
from datetime import datetime, timezone, timedelta

import pytest

from rss_morning.articles import ArticleContent
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
        lambda url: ArticleContent(
            text="article text", image="https://img.example.com"
        ),
    )
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
    article_image = "https://example.com/summary-image.jpg"

    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url: ArticleContent(text=None, image=article_image),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text: text)

    summary_payload = {"summaries": [{"url": "https://example.com"}]}

    def fake_generate(articles, system_prompt, return_dict):
        return json.dumps(summary_payload), summary_payload

    monkeypatch.setattr(runner, "generate_summary", fake_generate)
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
    assert rendered["summaries"]
    assert rendered["summaries"][0]["image"] == article_image
    assert result.is_summary
    assert calls[0]["is_summary"] is True
    assert calls[0]["subject"] == "RSS Mailer update for 1999-12-31 at 23:59"
    assert calls[0]["payload"]["summaries"][0]["image"] == article_image


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
        lambda url: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text: "trimmed")

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


def test_execute_pre_filter_applies_when_enabled(monkeypatch):
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
        lambda url: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    capture = {}

    class FakeFilter:
        class FakeConfig:
            model = "test-model"
            batch_size = 1
            threshold = 0.5

            def __init__(
                self,
                model=None,
                batch_size=None,
                threshold=None,
                max_article_length=None,
            ):
                self.model = model
                self.batch_size = batch_size
                self.threshold = threshold

        CONFIG = FakeConfig()

        def __init__(self, *args, **kwargs):
            capture["instantiated"] = True
            capture["query_path"] = kwargs.get("query_embeddings_path")

        def filter(self, articles, *, cluster_threshold=None, rng=None):
            capture["articles"] = list(articles)
            capture["cluster_threshold"] = cluster_threshold
            retained = [dict(articles[0])]
            retained[0]["url"] = "https://filtered.example.com"
            return retained

    import rss_morning.prefilter as prefilter_module

    monkeypatch.setattr(prefilter_module, "EmbeddingArticleFilter", FakeFilter)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        pre_filter=True,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    result = execute(config)
    payload = json.loads(result.output_text)

    assert capture["instantiated"] is True
    assert capture["query_path"] is None
    assert capture["articles"][0]["url"] == "https://example.com"
    assert capture["cluster_threshold"] == config.cluster_threshold
    assert len(payload) == 1
    assert payload[0]["url"] == "https://filtered.example.com"


def test_execute_pre_filter_skipped_when_disabled(monkeypatch):
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
        lambda url: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    class FailingFilter:
        def __init__(self, *args, **kwargs):
            raise AssertionError("pre-filter should not be instantiated")

    import rss_morning.prefilter as prefilter_module

    monkeypatch.setattr(prefilter_module, "EmbeddingArticleFilter", FailingFilter)

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        pre_filter=False,
        email_to=None,
        email_from=None,
        email_subject=None,
    )

    result = execute(config)
    payload = json.loads(result.output_text)

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
        pre_filter=False,
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
        lambda url: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text: "trimmed")
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    save_path = tmp_path / "fetched.json"

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        pre_filter=False,
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
        lambda url: ArticleContent(text=None, image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text: text)
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
        lambda url: ArticleContent(text="text", image=None),
    )
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
