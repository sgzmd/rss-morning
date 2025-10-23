import importlib
import sys
import time
import types
from datetime import datetime, timezone, timedelta

from rss_morning.models import FeedConfig, FeedEntry


def _reload_feeds_with_stub(monkeypatch, entries):
    stub_module = types.SimpleNamespace(
        parse=lambda url: types.SimpleNamespace(entries=entries),
    )
    monkeypatch.setitem(sys.modules, "feedparser", stub_module)
    sys.modules.pop("rss_morning.feeds", None)
    return importlib.import_module("rss_morning.feeds")


def test_fetch_feed_entries_extracts_basic_fields(monkeypatch):
    published = time.gmtime()
    entry = types.SimpleNamespace(
        link="https://example.com/a",
        title="Example Article",
        summary="  summary text ",
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
    assert parsed_entry.summary == "summary text"
    assert parsed_entry.category == "Cat"
    assert parsed_entry.published.tzinfo == timezone.utc


def test_fetch_feed_entries_falls_back_to_content(monkeypatch):
    published = time.gmtime()
    entry = types.SimpleNamespace(
        link="https://example.com/a",
        title="Example Article",
        summary=None,
        summary_detail=None,
        content=[{"value": "content summary"}],
        published_parsed=published,
    )
    feeds_module = _reload_feeds_with_stub(monkeypatch, [entry])

    feed = FeedConfig(
        category="Cat", title="Feed Title", url="https://feed.example.com"
    )
    results = feeds_module.fetch_feed_entries(feed)

    assert results[0].summary == "content summary"


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
