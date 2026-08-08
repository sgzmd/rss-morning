import threading
from datetime import datetime, timezone

import rss_morning.runner as runner
from rss_morning.articles import ArticleContent
from rss_morning.models import FeedConfig, FeedEntry
from rss_morning.runner import RunConfig, execute


class _ConcurrentCallProbe:
    """Record peak overlap while holding calls at a shared synchronization point."""

    def __init__(self, participants):
        self._barrier = threading.Barrier(participants)
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0

    def rendezvous(self):
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        try:
            self._barrier.wait(timeout=10)
        finally:
            with self._lock:
                self._active -= 1


def test_execute_runs_feed_and_article_stages_in_parallel(monkeypatch):
    """Verify both worker pools overlap calls without relying on wall-clock timing."""
    item_count = 5
    feed_probe = _ConcurrentCallProbe(item_count)
    article_probe = _ConcurrentCallProbe(item_count)

    def fetch_feed_entries(feed):
        feed_probe.rendezvous()
        return [
            FeedEntry(
                link=f"{feed.url}/post",
                category="Cat",
                title="Title",
                published=datetime(2024, 1, 1, tzinfo=timezone.utc),
                summary="Summary",
            )
        ]

    def fetch_article_content(_url, **_kwargs):
        article_probe.rendezvous()
        return ArticleContent(text="content", image=None)

    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [
            FeedConfig("Cat", f"Feed {i}", f"https://feed-{i}.example")
            for i in range(item_count)
        ],
    )
    monkeypatch.setattr(runner, "fetch_feed_entries", fetch_feed_entries)
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, _limit, _cutoff: entries
    )
    monkeypatch.setattr(runner, "fetch_article_content", fetch_article_content)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **_kwargs: text)
    monkeypatch.setattr(runner, "send_email_report", lambda **_kwargs: None)

    result = execute(
        RunConfig(
            feeds_file="unused.opml",
            limit=10,
            max_age_hours=None,
            summary=False,
            extractor="newspaper",
            concurrency=item_count,
        )
    )

    assert feed_probe.peak == item_count
    assert article_probe.peak == item_count
    assert len(result.email_payload) == item_count
