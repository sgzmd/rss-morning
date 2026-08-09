import requests.adapters  # noqa: F401

import json
import threading
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
    article_image = "https://example.com/summary-image.jpg"

    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text=None, image=article_image),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)

    summary_payload = {"summaries": [{"url": "https://example.com"}]}

    def fake_generate(articles, system_prompt, return_dict, **kwargs):
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
        lambda url, **kwargs: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")
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
                provider=None,
                batch_size=None,
                threshold=None,
                max_article_length=None,
            ):
                self.model = model
                self.provider = provider
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
        lambda url, **kwargs: ArticleContent(text="article text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: "trimmed")
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


def test_execute_with_database_caching(monkeypatch, tmp_path):
    """Verify that articles are cached and retrieved from the database."""
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda feed: [_feed_entry("https://example.com/db")],
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )

    # Mocking fetch to ensure it's called only once
    fetch_calls = []

    def fake_fetch(url, **kwargs):
        fetch_calls.append(url)
        return ArticleContent(text="Fetched Text", image="fetched.jpg")

    monkeypatch.setattr(runner, "fetch_article_content", fake_fetch)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    db_path = tmp_path / "test_rss.db"
    conn_str = f"sqlite:///{db_path}"

    config = RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        email_to=None,
        database_enabled=True,
        database_connection_string=conn_str,
    )

    # First run: Should fetch and cache
    result1 = execute(config)
    payload1 = json.loads(result1.output_text)

    assert len(fetch_calls) == 1
    assert payload1[0]["text"] == "Fetched Text"

    # Second run: Should use cache
    fetch_calls.clear()

    result2 = execute(config)
    payload2 = json.loads(result2.output_text)

    assert len(fetch_calls) == 0
    assert payload2[0]["text"] == "Fetched Text"


def test_execute_truncates_cached_content(monkeypatch, tmp_path):
    """Verify that entries served from cache are also truncated."""
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda path: [FeedConfig("Cat", "Feed", "url")]
    )
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda feed: [_feed_entry("https://example.com/db-trunc")],
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )

    # 1. First run stores a long string
    long_text = "This is a very long text that should be truncated." * 20
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text=long_text, image=None),
    )
    # On first run, we allow it to be stored full length (mocking truncate to no-op for storage simulation)
    # OR we simulate that it WAS stored with old logic (long text).
    # To simulate old storage, we can manually insert into DB or just allow truncate to return full text on first run.
    # Let's say existing DB has long text.
    # We'll rely on the fact that we can seed the DB or just run twice.
    # If we run with a large limit first, then small limit second.

    db_path = tmp_path / "test_rss_trunc.db"
    conn_str = f"sqlite:///{db_path}"

    config_large = RunConfig(
        feeds_file="feeds.xml",
        limit=1,
        max_age_hours=None,
        summary=False,
        email_to=None,
        database_enabled=True,
        database_connection_string=conn_str,
        max_article_length=10000,  # Large limit
    )

    # Use a real truncate or mock that respects limit?
    # runner.truncate_text is imported. Let's mock it to behave "real-ish" or simply return full text if limit is high.
    monkeypatch.setattr(
        runner,
        "truncate_text",
        lambda text, limit=100: text[:limit] if text else text,
    )

    execute(config_large)

    # 2. Second run with small limit should return truncated text from cache
    config_small = RunConfig(
        feeds_file="feeds.xml",
        limit=1,
        max_age_hours=None,
        summary=False,
        email_to=None,
        database_enabled=True,
        database_connection_string=conn_str,
        max_article_length=10,  # Small limit
    )

    result = execute(config_small)
    payload = json.loads(result.output_text)

    # Should be truncated to 10 chars
    assert len(payload[0]["text"]) == 10
    assert payload[0]["text"] == long_text[:10]


def test_cached_article_without_text_is_retried_and_kept_as_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda _path: [FeedConfig("New", "Feed", "url")]
    )
    feed_item = _feed_entry("https://example.com/missing-text")
    feed_item.category = "New"
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda _feed: [feed_item])
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )
    fetch_calls = []

    def failed_extract(url, **_kwargs):
        fetch_calls.append(url)
        return ArticleContent(text=None, image=None)

    monkeypatch.setattr(runner, "fetch_article_content", failed_extract)
    connection = f"sqlite:///{tmp_path / 'cache.db'}"
    engine = runner.db.init_engine(connection)
    assert engine is not None
    session_factory = runner.db.get_session_factory(engine)
    with session_factory() as session:
        runner.db.upsert_article(
            session,
            {
                "url": feed_item.link,
                "title": "Old title",
                "text": None,
                "image": "old.jpg",
            },
        )

    result = execute(
        RunConfig(
            feeds_file="feeds.xml",
            limit=1,
            max_age_hours=None,
            summary=False,
            database_enabled=True,
            database_connection_string=connection,
        )
    )

    assert fetch_calls == [feed_item.link]
    assert json.loads(result.output_text) == [
        {
            "url": feed_item.link,
            "category": "New",
            "title": "Title",
            "summary": "Summary",
            "published": "2024-01-01T00:00:00+00:00",
        }
    ]
    engine.dispose()


def test_successful_extraction_caches_raw_text_before_output_truncation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda _path: [FeedConfig("Cat", "Feed", "url")]
    )
    feed_item = _feed_entry("https://example.com/raw-cache")
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda _feed: [feed_item])
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )
    raw_text = "abcdefghijklmnopqrstuvwxyz"
    fetch_calls = []

    def extract(url, **_kwargs):
        fetch_calls.append(url)
        return ArticleContent(text=raw_text, image="image.jpg")

    monkeypatch.setattr(runner, "fetch_article_content", extract)
    monkeypatch.setattr(runner, "truncate_text", lambda text, limit: text[:limit])
    connection = f"sqlite:///{tmp_path / 'raw-cache.db'}"
    first_config = RunConfig(
        feeds_file="feeds.xml",
        limit=1,
        max_age_hours=None,
        summary=False,
        database_enabled=True,
        database_connection_string=connection,
        max_article_length=10,
    )

    first = execute(first_config)
    assert json.loads(first.output_text)[0]["text"] == raw_text[:10]

    engine = runner.db.init_engine(connection)
    assert engine is not None
    session_factory = runner.db.get_session_factory(engine)
    with session_factory() as session:
        cached = runner.db.get_article(session, feed_item.link)
    assert cached is not None
    assert cached["text"] == raw_text

    fetch_calls.clear()
    second = execute(
        RunConfig(
            **{
                **first_config.__dict__,
                "max_article_length": 100,
            }
        )
    )
    assert json.loads(second.output_text)[0]["text"] == raw_text
    assert fetch_calls == []
    engine.dispose()


def test_failed_extraction_is_not_written_to_article_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner, "parse_feeds_config", lambda _path: [FeedConfig("Cat", "Feed", "url")]
    )
    feed_item = _feed_entry("https://example.com/not-cached")
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda _feed: [feed_item])
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: ArticleContent(text=None, image=None),
    )
    connection = f"sqlite:///{tmp_path / 'failed.db'}"

    execute(
        RunConfig(
            feeds_file="feeds.xml",
            limit=1,
            max_age_hours=None,
            summary=False,
            database_enabled=True,
            database_connection_string=connection,
        )
    )

    engine = runner.db.init_engine(connection)
    assert engine is not None
    session_factory = runner.db.get_session_factory(engine)
    with session_factory() as session:
        assert runner.db.get_article(session, feed_item.link) is None
    engine.dispose()


def test_cached_article_restores_fields_but_feed_category_wins(monkeypatch, tmp_path):
    feed_item = _feed_entry("https://example.com/restored")
    feed_item.category = "Current category"
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [FeedConfig("Current category", "Feed", "url")],
    )
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda _feed: [feed_item])
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit must not fetch")
        ),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, limit: text)
    connection = f"sqlite:///{tmp_path / 'restored.db'}"
    engine = runner.db.init_engine(connection)
    assert engine is not None
    session_factory = runner.db.get_session_factory(engine)
    published = datetime(2023, 5, 6, tzinfo=timezone.utc)
    with session_factory() as session:
        runner.db.upsert_article(
            session,
            {
                "url": feed_item.link,
                "title": "Cached title",
                "summary": "Cached summary",
                "text": "Cached text",
                "image": "cached.jpg",
                "published": published,
            },
        )

    result = execute(
        RunConfig(
            feeds_file="feeds.xml",
            limit=1,
            max_age_hours=None,
            summary=False,
            database_enabled=True,
            database_connection_string=connection,
        )
    )

    assert json.loads(result.output_text) == [
        {
            "url": feed_item.link,
            "category": "Current category",
            "title": "Cached title",
            "summary": "Cached summary",
            "text": "Cached text",
            "image": "cached.jpg",
            "published": "2023-05-06T00:00:00",
        }
    ]
    engine.dispose()


def test_cache_read_failure_drops_only_affected_article(monkeypatch):
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [FeedConfig("Cat", "Feed", "url")],
    )
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda _feed: [_feed_entry("bad"), _feed_entry("good")],
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )
    monkeypatch.setattr(runner.db, "init_engine", lambda _connection: object())

    class SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        runner.db, "get_session_factory", lambda _engine: SessionContext
    )

    def get_article(_session, url):
        if url == "bad":
            raise RuntimeError("cache read failed")
        return None

    monkeypatch.setattr(runner.db, "get_article", get_article)
    monkeypatch.setattr(runner.db, "upsert_article", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: ArticleContent(text="text", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, limit: text)

    result = execute(
        RunConfig(
            feeds_file="feeds.xml",
            limit=2,
            max_age_hours=None,
            summary=False,
            database_enabled=True,
            database_connection_string="sqlite://",
        )
    )

    assert [item["url"] for item in json.loads(result.output_text)] == ["good"]


def test_article_cache_uses_one_bulk_read_and_write_outside_workers(monkeypatch):
    now = datetime(2025, 1, 3, tzinfo=timezone.utc)
    entries = [
        FeedEntry("hit", "B", "Hit feed title", now, "Hit feed summary"),
        FeedEntry("new", "A", "New", now - timedelta(hours=1), "New summary"),
        FeedEntry(
            "metadata", "A", "Metadata", now - timedelta(hours=2), "Metadata summary"
        ),
        FeedEntry("new", "A", "Duplicate", now - timedelta(hours=3), "Duplicate"),
    ]
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [FeedConfig("Cat", "Feed", "feed")],
    )
    monkeypatch.setattr(runner, "fetch_feed_entries", lambda _feed: entries)
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda values, _limit, _cutoff: values
    )
    monkeypatch.setattr(runner, "prepare_tokenizer", lambda: None)
    monkeypatch.setattr(runner, "truncate_text", lambda text, limit: text[:limit])

    def extract(url, **_kwargs):
        if url == "metadata":
            return ArticleContent(text=None, image=None)
        if url == "hit":
            raise AssertionError("cache hit must not fetch")
        return ArticleContent(text="new full text", image="new.jpg")

    monkeypatch.setattr(runner, "fetch_article_content", extract)
    main_thread = threading.get_ident()
    session_threads = []

    class SessionContext:
        def __enter__(self):
            session_threads.append(threading.get_ident())
            return object()

        def __exit__(self, *_args):
            return None

    class SessionFactory:
        def __call__(self):
            return SessionContext()

    bulk_reads = []
    bulk_writes = []
    cached_hit = {
        "url": "hit",
        "title": "Cached title",
        "text": "cached text",
        "image": "cached.jpg",
        "summary": "Cached summary",
        "published": now,
    }
    monkeypatch.setattr(
        runner.db,
        "get_articles",
        lambda _session, urls: bulk_reads.append(list(urls)) or {"hit": cached_hit},
        raising=False,
    )
    monkeypatch.setattr(
        runner.db,
        "upsert_articles",
        lambda _session, payloads: bulk_writes.append(list(payloads)),
        raising=False,
    )
    monkeypatch.setattr(
        runner.db,
        "get_article",
        lambda _session, url: cached_hit if url == "hit" else None,
    )
    monkeypatch.setattr(runner.db, "upsert_article", lambda *_args: None)

    output = runner._collect_entries(
        RunConfig(
            feeds_file="feeds.xml",
            limit=10,
            max_age_hours=None,
            summary=False,
            max_article_length=100,
            concurrency=3,
        ),
        session_factory=SessionFactory(),
    )

    assert bulk_reads == [["hit", "new", "metadata"]]
    assert len(bulk_writes) == 1
    assert [payload["url"] for payload in bulk_writes[0]] == ["new"]
    assert session_threads == [main_thread, main_thread]
    assert [item["url"] for item in output] == ["new", "metadata", "hit"]
    assert output[2]["category"] == "B"
