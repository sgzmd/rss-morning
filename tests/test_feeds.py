import importlib
import sys
import time
import types
from datetime import datetime, timezone, timedelta

from rss_morning.models import FeedConfig, FeedEntry


def _reload_feeds_with_stub(monkeypatch, entries):
    # Stub feedparser
    stub_feedparser = types.SimpleNamespace(
        parse=lambda content: types.SimpleNamespace(entries=entries),
    )
    monkeypatch.setitem(sys.modules, "feedparser", stub_feedparser)

    sys.modules.pop("rss_morning.feeds", None)
    feeds_module = importlib.import_module("rss_morning.feeds")
    feeds_module._DEFAULT_HTTP_CLIENT = types.SimpleNamespace(
        get=lambda *_args, **_kwargs: types.SimpleNamespace(body=b"mock content")
    )
    return feeds_module


def test_fetch_feed_entries_strips_html_from_summary(monkeypatch):
    published = time.gmtime()
    entry = types.SimpleNamespace(
        link="https://example.com/a",
        title="Example Article",
        summary="  <p>Summary <strong>text</strong> with a <a href='#'>link</a>.</p> ",
        published_parsed=published,
    )
    feeds_module = _reload_feeds_with_stub(monkeypatch, [entry])

    feed = FeedConfig(
        category="Cat", title="Feed Title", url="https://feed.example.com"
    )
    results = feeds_module.fetch_feed_entries(feed)

    assert len(results) == 1
    parsed_entry = results[0]
    assert parsed_entry.link == "https://example.com/a"
    assert parsed_entry.summary == "Summary text with a link."
    assert parsed_entry.category == "Cat"
    assert parsed_entry.published.tzinfo == timezone.utc


def test_fetch_feed_entries_falls_back_to_content_and_strips_html(monkeypatch):
    published = time.gmtime()
    entry = types.SimpleNamespace(
        link="https://example.com/a",
        title="Example Article",
        summary=None,
        summary_detail=None,
        content=[{"value": "<div>content <em>summary</em></div>"}],
        published_parsed=published,
    )
    feeds_module = _reload_feeds_with_stub(monkeypatch, [entry])

    feed = FeedConfig(
        category="Cat", title="Feed Title", url="https://feed.example.com"
    )
    results = feeds_module.fetch_feed_entries(feed)

    assert results[0].summary == "content summary"


def test_fetch_feed_entries_uses_summary_detail_and_strips_html(monkeypatch):
    published = time.gmtime()
    entry = types.SimpleNamespace(
        link="https://example.com/b",
        title="Example Article",
        summary=None,
        summary_detail={"value": "<p>Detail <span>summary</span></p>"},
        content=None,
        published_parsed=published,
    )
    feeds_module = _reload_feeds_with_stub(monkeypatch, [entry])

    feed = FeedConfig(
        category="Cat", title="Feed Title", url="https://feed.example.com"
    )
    results = feeds_module.fetch_feed_entries(feed)

    assert results[0].summary == "Detail summary"


def test_select_recent_entries_deduplicates_and_applies_cutoff(monkeypatch):
    feeds_module = _reload_feeds_with_stub(monkeypatch, [])

    now = datetime.now(timezone.utc)
    entries = [
        FeedEntry(link="1", category="C", title="A", published=now),
        FeedEntry(
            link="1",
            category="C",
            title="A older",
            published=now - timedelta(minutes=5),
        ),
        FeedEntry(link="2", category="C", title="B", published=now - timedelta(days=2)),
    ]

    selected = feeds_module.select_recent_entries(
        entries, limit=5, cutoff=now - timedelta(days=1)
    )

    assert [entry.link for entry in selected] == ["1"]


def test_fetch_feed_entries_handles_request_exception(monkeypatch):
    feeds_module = _reload_feeds_with_stub(monkeypatch, [])

    class FailingClient:
        def get(self, *_args, **_kwargs):
            raise feeds_module.DownloadError("Timeout")

    feed = FeedConfig(
        category="Cat", title="Feed Title", url="https://timeout.example.com"
    )
    results = feeds_module.fetch_feed_entries(feed, http_client=FailingClient())

    assert results == []


def test_fetch_feed_entries_uses_injected_http_client(monkeypatch):
    feeds_module = _reload_feeds_with_stub(monkeypatch, [])
    calls = []

    class Client:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return types.SimpleNamespace(body=b"<rss />")

    feed = FeedConfig(category="Cat", title="Feed", url="https://feed.example.com")

    assert feeds_module.fetch_feed_entries(feed, http_client=Client()) == []
    assert calls == [
        (
            feed.url,
            {
                "accepted_content_types": (
                    "application/rss+xml",
                    "application/atom+xml",
                    "application/xml",
                    "text/xml",
                ),
                "allow_missing_content_type": True,
            },
        )
    ]


def test_fetch_feed_entries_covers_missing_fields_and_date_fallbacks(monkeypatch):
    stamp = time.gmtime(1)
    entries = [
        types.SimpleNamespace(link=None, title="No link"),
        types.SimpleNamespace(
            link="https://example.com/bad-content",
            title="Bad content",
            summary=None,
            summary_detail=None,
            content=[None],
            published_parsed=None,
            updated_parsed=None,
            created_parsed=None,
        ),
        types.SimpleNamespace(
            link="https://example.com/updated",
            title="Updated",
            summary=None,
            summary_detail=None,
            content=None,
            published_parsed=None,
            updated_parsed=stamp,
        ),
        types.SimpleNamespace(
            link="https://example.com/created",
            title="Created",
            summary=None,
            summary_detail=None,
            content=None,
            published_parsed=None,
            updated_parsed=None,
            created_parsed=stamp,
        ),
    ]
    feeds_module = _reload_feeds_with_stub(monkeypatch, entries)

    results = feeds_module.fetch_feed_entries(
        FeedConfig(category="Cat", title="Feed", url="https://feed.example.com")
    )

    assert [item.summary for item in results] == [None, None, None]
    assert results[0].published == datetime.min.replace(tzinfo=timezone.utc)
    assert results[1].published == feeds_module.to_datetime(stamp)
    assert results[2].published == feeds_module.to_datetime(stamp)


def test_select_recent_entries_stops_at_limit_without_cutoff(monkeypatch):
    feeds_module = _reload_feeds_with_stub(monkeypatch, [])
    now = datetime.now(timezone.utc)
    entries = [
        FeedEntry(link="1", category="C", title="A", published=now),
        FeedEntry(link="2", category="C", title="B", published=now),
    ]

    assert [item.link for item in feeds_module.select_recent_entries(entries, 1)] == [
        "1"
    ]
