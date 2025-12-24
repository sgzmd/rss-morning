import time
from datetime import datetime, timezone
from rss_morning.models import FeedConfig, FeedEntry
from rss_morning.articles import ArticleContent
from rss_morning.runner import RunConfig, execute
import rss_morning.runner as runner


def test_execute_runs_in_parallel(monkeypatch):
    """Verify that execution time is significantly less than serial execution time."""

    # Simulate slow operations
    DELAY = 0.5
    NUM_ITEMS = 5

    def slow_fetch_feed_entries(feed):
        time.sleep(DELAY)
        return [
            FeedEntry(
                link=f"{feed.url}/post",
                category="Cat",
                title="Title",
                published=datetime.now(timezone.utc),
                summary="Summary",
            )
        ]

    def slow_fetch_article_content(url, **kwargs):
        time.sleep(DELAY)
        return ArticleContent(text="content", image=None)

    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda path: [
            FeedConfig("Cat", f"Feed {i}", f"http://feed-{i}.com")
            for i in range(NUM_ITEMS)
        ],
    )
    monkeypatch.setattr(runner, "fetch_feed_entries", slow_fetch_feed_entries)
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(runner, "fetch_article_content", slow_fetch_article_content)
    monkeypatch.setattr(runner, "truncate_text", lambda text: text)
    monkeypatch.setattr(runner, "send_email_report", lambda **kwargs: None)

    # With concurrency=10, 5 items should take roughly DELAY seconds (feeds) + DELAY seconds (articles)
    # Total ~ 2*DELAY.
    # Serial would be 5*DELAY (feeds) + 5*DELAY (articles) = 10*DELAY.

    config = RunConfig(
        feeds_file="dummy",
        limit=10,
        max_age_hours=None,
        summary=False,
        extractor="newspaper",
        concurrency=NUM_ITEMS,
    )

    start = time.time()
    execute(config)
    duration = time.time() - start

    # Allow some overhead, but it should be much faster than serial
    # Serial time would be around 5s (0.5 * 10)
    # Parallel time should be around 1s (0.5 * 2)

    assert (
        duration < (DELAY * NUM_ITEMS * 2) / 2
    )  # Should be less than half of serial time
    assert duration > DELAY * 2  # At least wait for the delays
