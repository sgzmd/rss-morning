"""Behavior contract for persistent conditional RSS downloads."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from rss_morning import db, feeds, runner
from rss_morning.articles import ArticleContent
from rss_morning.models import FeedConfig, FeedEntry


@pytest.fixture
def session():
    engine = db.init_engine("sqlite:///:memory:")
    session_factory = db.get_session_factory(engine)
    database_session = session_factory()
    yield database_session
    database_session.close()
    engine.dispose()


def rss_body(title: str = "Story") -> bytes:
    return f"""<?xml version="1.0"?><rss version="2.0"><channel>
    <title>Feed</title><item><title>{title}</title>
    <link>https://articles.example/{title.lower()}</link>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
    </item></channel></rss>""".encode()


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def response(
    status: int,
    *,
    body: bytes = b"",
    final_url: str = "https://cdn.example/feed.xml",
    etag: str | None = None,
    last_modified: str | None = None,
):
    headers = {}
    if etag:
        headers["etag"] = etag
    if last_modified:
        headers["last-modified"] = last_modified
    return SimpleNamespace(
        status=status, body=body, final_url=final_url, headers=headers
    )


def feed_config(url: str = "https://feeds.example/rss") -> FeedConfig:
    return FeedConfig(category="News", title="Example", url=url)


def cached_state(body: bytes | None = None) -> dict:
    return {
        "configured_url": "https://feeds.example/rss",
        "final_url": "https://cdn.example/feed.xml",
        "etag": '"v1"',
        "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        "body": rss_body() if body is None else body,
        "fetched_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }


def test_200_stores_bounded_body_validators_timestamp_and_redirect_identity():
    updates = []
    client = FakeClient(
        response(
            200,
            body=rss_body(),
            etag='"v1"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )
    )

    entries = feeds.fetch_feed_entries(
        feed_config(), http_client=client, on_cache_update=updates.append
    )

    assert [entry.title for entry in entries] == ["Story"]
    assert len(updates) == 1
    assert updates[0]["configured_url"] == "https://feeds.example/rss"
    assert updates[0]["final_url"] == "https://cdn.example/feed.xml"
    assert updates[0]["etag"] == '"v1"'
    assert updates[0]["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert updates[0]["body"] == rss_body()
    assert updates[0]["fetched_at"].tzinfo == timezone.utc


def test_304_sends_validators_and_parses_cached_body_without_transfer():
    state = cached_state()
    client = FakeClient(response(304))

    outcomes = []
    entries = feeds.fetch_feed_entries(
        feed_config(),
        http_client=client,
        cached_state=state,
        on_result=outcomes.append,
    )

    assert [entry.title for entry in entries] == ["Story"]
    assert client.calls[0][1]["headers"] == {
        "If-None-Match": '"v1"',
        "If-Modified-Since": "Wed, 01 Jan 2025 00:00:00 GMT",
    }
    assert client.responses == []
    assert outcomes == [True]


@pytest.mark.parametrize(
    "removed,expected",
    [
        ("etag", {"If-Modified-Since": "Wed, 01 Jan 2025 00:00:00 GMT"}),
        ("last_modified", {"If-None-Match": '"v1"'}),
    ],
)
def test_conditional_request_uses_each_available_validator(removed, expected):
    state = cached_state()
    state.pop(removed)
    client = FakeClient(response(304))

    feeds.fetch_feed_entries(feed_config(), http_client=client, cached_state=state)

    assert client.calls[0][1]["headers"] == expected


@pytest.mark.parametrize("bad_body", [None, b"not valid feed xml"])
def test_304_bad_cache_makes_one_unconditional_recovery_request(bad_body):
    state = cached_state()
    state["body"] = bad_body
    client = FakeClient(response(304), response(200, body=rss_body("Recovered")))

    entries = feeds.fetch_feed_entries(
        feed_config(), http_client=client, cached_state=state
    )

    assert [entry.title for entry in entries] == ["Recovered"]
    assert len(client.calls) == 2
    assert client.calls[0][1]["headers"]
    assert client.calls[1][1].get("headers") in (None, {})


def test_changed_200_replaces_prior_state_with_one_atomic_database_commit(session):
    old = cached_state()
    db.upsert_feed_http_states(session, [old])
    replacement = {
        **old,
        "body": rss_body("Changed"),
        "etag": '"v2"',
        "fetched_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
    }
    commits = []
    original_commit = session.commit

    def commit_once():
        commits.append(True)
        original_commit()

    session.commit = commit_once
    db.upsert_feed_http_states(session, [replacement])

    stored = db.get_feed_http_states(session, [old["configured_url"]])
    assert commits == [True]
    assert stored[old["configured_url"]]["body"] == rss_body("Changed")
    assert stored[old["configured_url"]]["etag"] == '"v2"'


def test_feed_database_bulk_helpers_handle_empty_invalid_and_rollback(session):
    assert db.get_feed_http_states(session, []) == {}
    db.upsert_feed_http_states(
        session,
        [{}, {"configured_url": "bad", "body": "not bytes"}],
    )

    from unittest.mock import MagicMock

    failing_session = MagicMock()
    failing_session.execute.return_value.scalars.return_value.all.return_value = []
    failing_session.commit.side_effect = RuntimeError("commit failed")
    with pytest.raises(RuntimeError, match="commit failed"):
        db.upsert_feed_http_states(
            failing_session,
            [{"configured_url": "feed", "body": b"body"}],
        )
    failing_session.rollback.assert_called_once_with()


def test_feed_state_is_bulk_read_and_written_outside_workers(monkeypatch):
    configured_feeds = [
        feed_config("https://feeds.example/one"),
        feed_config("https://feeds.example/two"),
    ]
    monkeypatch.setattr(runner, "parse_feeds_config", lambda _path: configured_feeds)
    reads = []
    writes = []

    monkeypatch.setattr(
        runner.db,
        "get_feed_http_states",
        lambda _session, urls: reads.append(urls) or {},
    )
    monkeypatch.setattr(
        runner.db,
        "upsert_feed_http_states",
        lambda _session, states: writes.append(states),
    )

    def fetch(feed, *, on_cache_update, **_kwargs):
        on_cache_update({"configured_url": feed.url, "body": rss_body()})
        return [
            FeedEntry(
                link=f"https://articles.example/{feed.title}",
                category=feed.category,
                title=feed.title,
                published=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(runner, "fetch_feed_entries", fetch)
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: ArticleContent(text=None, image=None),
    )
    monkeypatch.setattr(runner.db, "get_articles", lambda *_args: {})

    class SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    runner._collect_entries(
        runner.RunConfig("feeds.xml", 1, None, False),
        session_factory=SessionContext,
    )

    assert reads == [[feed.url for feed in configured_feeds]]
    assert len(writes) == 1
    assert {state["configured_url"] for state in writes[0]} == {
        feed.url for feed in configured_feeds
    }

    monkeypatch.setattr(
        runner.db,
        "upsert_feed_http_states",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("write failed")),
    )
    assert runner._collect_entries(
        runner.RunConfig("feeds.xml", 1, None, False),
        session_factory=SessionContext,
    )


def test_database_disabled_run_never_reads_or_writes_feed_state(monkeypatch):
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [feed_config()],
    )
    monkeypatch.setattr(
        runner.db,
        "get_feed_http_states",
        lambda *_args: pytest.fail("disabled run must not read feed cache"),
        raising=False,
    )
    monkeypatch.setattr(
        runner.db,
        "upsert_feed_http_states",
        lambda *_args: pytest.fail("disabled run must not write feed cache"),
        raising=False,
    )
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda _feed, **_kwargs: [
            FeedEntry(
                link="https://articles.example/story",
                category="News",
                title="Story",
                published=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        ],
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: ArticleContent(text=None, image=None),
    )

    runner._collect_entries(runner.RunConfig("feeds.xml", 1, None, False))


def test_corrupt_cache_recovery_is_isolated_to_its_feed():
    corrupt = cached_state(b"broken")
    valid = cached_state()
    valid["configured_url"] = "https://feeds.example/valid"
    corrupt_client = FakeClient(response(304), response(200, body=rss_body("Fresh")))
    valid_client = FakeClient(response(304))

    corrupt_entries = feeds.fetch_feed_entries(
        feed_config(), http_client=corrupt_client, cached_state=corrupt
    )
    valid_entries = feeds.fetch_feed_entries(
        feed_config(valid["configured_url"]),
        http_client=valid_client,
        cached_state=valid,
    )

    assert [entry.title for entry in corrupt_entries] == ["Fresh"]
    assert [entry.title for entry in valid_entries] == ["Story"]
    assert len(corrupt_client.calls) == 2
    assert len(valid_client.calls) == 1


def test_malformed_current_response_does_not_replace_last_good_body():
    updates = []
    client = FakeClient(response(200, body=b"not a feed"))

    outcomes = []
    entries = feeds.fetch_feed_entries(
        feed_config(),
        http_client=client,
        cached_state=cached_state(),
        on_cache_update=updates.append,
        on_result=outcomes.append,
    )

    assert entries == []
    assert updates == []
    assert outcomes == [False]
    assert (
        feeds.fetch_feed_entries(
            feed_config(),
            http_client=FakeClient(response(200, body=b"not a feed")),
        )
        == []
    )


def test_transient_error_preserves_state_but_does_not_serve_it():
    updates = []

    class FailingClient:
        def get(self, *_args, **_kwargs):
            raise feeds.DownloadError("temporary")

    entries = feeds.fetch_feed_entries(
        feed_config(),
        http_client=FailingClient(),
        cached_state=cached_state(),
        on_cache_update=updates.append,
    )

    assert entries == []
    assert updates == []
