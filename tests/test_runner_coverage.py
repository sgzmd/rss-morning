"""Behavior coverage for orchestration fallbacks and validation."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from rss_morning import runner
from rss_morning.articles import ArticleContent
from rss_morning.models import FeedConfig, FeedEntry


def config(**overrides):
    values = dict(feeds_file="feeds.xml", limit=2, max_age_hours=None, summary=False)
    values.update(overrides)
    return runner.RunConfig(**values)


def entry(url="https://example.com/a", category="Cat"):
    return FeedEntry(
        link=url,
        category=category,
        title="Title",
        published=datetime(2025, 1, 1, tzinfo=timezone.utc),
        summary="Summary",
    )


def test_article_snapshot_validation_and_nested_save(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        runner._load_articles_from_file(str(tmp_path / "missing.json"))

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("bad json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        runner._load_articles_from_file(str(snapshot))

    snapshot.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="JSON array"):
        runner._load_articles_from_file(str(snapshot))

    snapshot.write_text("[1]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="objects only"):
        runner._load_articles_from_file(str(snapshot))

    destination = tmp_path / "nested" / "articles.json"
    runner._save_articles_to_file(str(destination), [{"title": "A"}])
    assert json.loads(destination.read_text()) == [{"title": "A"}]


def test_collect_entries_requires_feeds(monkeypatch):
    monkeypatch.setattr(runner, "parse_feeds_config", lambda _path: [])
    with pytest.raises(RuntimeError, match="No feeds found"):
        runner._collect_entries(config())


def test_collect_entries_applies_cutoff_deduplicates_and_recovers_feed_failure(
    monkeypatch,
):
    feeds = [
        FeedConfig("Cat", "Bad", "bad"),
        FeedConfig("Cat", "Good", "good"),
        FeedConfig("Cat", "Empty", "empty"),
    ]
    monkeypatch.setattr(runner, "parse_feeds_config", lambda _path: feeds)

    def fetch(feed):
        if feed.url == "bad":
            raise RuntimeError("feed failed")
        if feed.url == "empty":
            return []
        return [entry(), entry()]

    cutoffs = []
    monkeypatch.setattr(runner, "fetch_feed_entries", fetch)
    monkeypatch.setattr(
        runner,
        "select_recent_entries",
        lambda entries, _limit, cutoff: cutoffs.append(cutoff) or entries,
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: ArticleContent(text=None, image=None),
    )

    result = runner._collect_entries(config(max_age_hours=1))

    assert len(result) == 1
    assert cutoffs[0] is not None


def test_collect_entries_drops_failed_article_but_keeps_success(monkeypatch):
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [FeedConfig("Cat", "Feed", "feed")],
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda _feed: [entry("bad"), entry("good")]
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )

    def fetch(url, **_kwargs):
        if url == "bad":
            raise RuntimeError("article failed")
        return ArticleContent(text="body", image=None)

    monkeypatch.setattr(runner, "fetch_article_content", fetch)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **_kwargs: text)

    assert [item["url"] for item in runner._collect_entries(config())] == ["good"]


def test_attach_summary_images_all_guard_branches():
    source = [{"url": "u", "image": "image"}, {"url": "empty", "image": None}]
    assert runner._attach_summary_images([], source) == []
    assert runner._attach_summary_images({}, source) == {}
    assert runner._attach_summary_images({"summaries": "bad"}, source) == {
        "summaries": "bad"
    }
    payload = {"summaries": ["bad", {}, {"url": "u", "image": "existing"}]}
    assert runner._attach_summary_images(payload, source) == payload
    no_images = {"summaries": [{"url": "u"}]}
    assert runner._attach_summary_images(no_images, [{"url": "u"}]) == no_images
    missing = {"summaries": [{"url": "other"}]}
    assert runner._attach_summary_images(missing, source) == missing


def test_default_email_subject_has_expected_prefix():
    assert runner._build_default_email_subject().startswith("RSS Mailer update for ")


def test_execute_database_disabled_when_configuration_is_incomplete(monkeypatch):
    monkeypatch.setattr(runner, "_collect_entries", lambda *_args, **_kwargs: [])
    assert runner.execute(config(database_enabled=True)).email_payload == []

    monkeypatch.setattr(runner.db, "init_engine", lambda _connection: None)
    assert (
        runner.execute(
            config(database_enabled=True, database_connection_string="sqlite://")
        ).email_payload
        == []
    )


@pytest.mark.parametrize("limit", [0, -1])
def test_execute_rejects_nonpositive_article_limit_before_work(monkeypatch, limit):
    monkeypatch.setattr(
        runner,
        "_collect_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("article work must not start")
        ),
    )

    with pytest.raises(ValueError, match="max_article_length must be positive"):
        runner.execute(config(max_article_length=limit))


def test_execute_prefilter_none_fails_open(monkeypatch):
    monkeypatch.setattr(
        runner, "_collect_entries", lambda *_args, **_kwargs: [{"url": "original"}]
    )
    from rss_morning import prefilter

    class FakeFilter:
        CONFIG = SimpleNamespace(batch_size=1, threshold=0.5)

        def __init__(self, **_kwargs):
            pass

        def filter(self, *_args, **_kwargs):
            return None

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    FakeFilter.CONFIG = FakeConfig(batch_size=1, threshold=0.5)
    monkeypatch.setattr(prefilter, "EmbeddingArticleFilter", FakeFilter)

    assert json.loads(runner.execute(config(pre_filter=True)).output_text) == [
        {"url": "original"}
    ]


def test_execute_summary_validation_dry_run_and_missing_data(monkeypatch):
    monkeypatch.setattr(
        runner, "_collect_entries", lambda *_args, **_kwargs: [{"url": "u"}]
    )
    with pytest.raises(ValueError, match="system_prompt"):
        runner.execute(config(summary=True))

    monkeypatch.setattr(
        runner,
        "generate_summary",
        lambda *_args, **_kwargs: ('{"dry_run": true}', {"dry_run": True}),
    )
    result = runner.execute(
        config(summary=True, system_prompt="prompt", llm_dry_run=True, email_to="to")
    )
    assert result.email_payload is None
    assert result.is_summary is True

    monkeypatch.setattr(
        runner,
        "generate_summary",
        lambda *_args, **_kwargs: ('{"summaries": []}', None),
    )
    result = runner.execute(config(summary=True, system_prompt="prompt"))
    assert result.email_payload == [{"url": "u"}]
    assert result.is_summary is False

    monkeypatch.setattr(
        runner, "generate_summary", lambda *_args, **_kwargs: ("[]", ["summary"])
    )
    result = runner.execute(config(summary=True, system_prompt="prompt"))
    assert result.email_payload == ["summary"]
    assert result.is_summary is True
