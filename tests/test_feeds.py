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

    # Stub requests
    mock_response = types.SimpleNamespace(
        content=b"mock content",
        raise_for_status=lambda: None,
    )
    stub_requests = types.SimpleNamespace(
        get=lambda url, timeout=None: mock_response,
        RequestException=Exception,
    )
    monkeypatch.setitem(sys.modules, "requests", stub_requests)

    sys.modules.pop("rss_morning.feeds", None)
    return importlib.import_module("rss_morning.feeds")


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
    # Setup stub that raises RequestException
    _ = types.SimpleNamespace()

    # We need a proper exception class that looks like requests.RequestException
    class MockRequestException(Exception):
        pass

    stub_requests = types.SimpleNamespace(
        get=lambda url, timeout=None: (_ for _ in ()).throw(
            MockRequestException("Timeout")
        ),
        RequestException=MockRequestException,
    )

    monkeypatch.setitem(sys.modules, "requests", stub_requests)

    # Feedparser stub shouldn't matter as it won't be reached, but we provide it for import safety
    stub_feedparser = types.SimpleNamespace(parse=lambda *args: None)
    monkeypatch.setitem(sys.modules, "feedparser", stub_feedparser)

    sys.modules.pop("rss_morning.feeds", None)
    feeds_module = importlib.import_module("rss_morning.feeds")

    feed = FeedConfig(
        category="Cat", title="Feed Title", url="https://timeout.example.com"
    )
    results = feeds_module.fetch_feed_entries(feed)

    assert results == []
