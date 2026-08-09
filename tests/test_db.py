"""Tests for the database abstraction layer."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from rss_morning import db


@pytest.fixture
def session():
    """Create an in-memory SQLite session for testing."""
    engine = db.init_engine("sqlite:///:memory:")
    SessionLocal = db.get_session_factory(engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


def test_upsert_and_get_article(session):
    url = "https://example.com/article1"
    data = {
        "url": url,
        "title": "Test Title",
        "text": "Content",
        "image": "img.jpg",
        "summary": "Summary",
    }

    # Initial insert
    db.upsert_article(session, data)

    cached = db.get_article(session, url)
    assert cached is not None
    assert cached["url"] == url
    assert cached["title"] == "Test Title"

    # Update
    data["title"] = "Updated Title"
    db.upsert_article(session, data)

    cached = db.get_article(session, url)
    assert cached["title"] == "Updated Title"
    assert cached["text"] == "Content"


def test_upsert_article_parses_iso_publication_date(session):
    published = "2025-03-04T05:06:07+00:00"

    db.upsert_article(
        session,
        {"url": "https://example.com/dated", "published": published},
    )

    cached = db.get_article(session, "https://example.com/dated")
    assert cached is not None
    assert cached["published"].isoformat().startswith("2025-03-04T05:06:07")


def test_upsert_article_ignores_invalid_publication_date(session, caplog):
    db.upsert_article(
        session,
        {"url": "https://example.com/invalid-date", "published": "not-a-date"},
    )

    cached = db.get_article(session, "https://example.com/invalid-date")
    assert cached is not None
    assert cached["published"] is None
    assert "Ignoring invalid publication date" in caplog.text


def test_init_engine_does_not_log_connection_string(monkeypatch, caplog):
    connection_string = "postgresql://user:secret@example.com/rss"
    monkeypatch.setattr(db, "create_engine", lambda value: object())
    monkeypatch.setattr(db.Base.metadata, "create_all", lambda engine: None)

    caplog.set_level("INFO")
    db.init_engine(connection_string)

    assert "Initializing database connection" in caplog.text
    assert connection_string not in caplog.text
    assert "secret" not in caplog.text


def test_upsert_and_get_embeddings(session):
    url1 = "https://example.com/1"
    url2 = "https://example.com/2"
    backend = "test-model"

    vec1 = [0.1, 0.2]
    vec2 = [0.3, 0.4]

    # Convert to JSON bytes as per implementation in prefilter.py
    # But wait, db.py takes bytes directly. prefilter.py does the encoding.
    # So here we pass bytes.
    data = {
        url1: json.dumps(vec1).encode("utf-8"),
        url2: json.dumps(vec2).encode("utf-8"),
    }

    db.upsert_embeddings(session, data, backend)

    # Fetch
    cached = db.get_embeddings(session, [url1, url2, "missing"], backend)

    assert len(cached) == 2
    assert cached[url1] == data[url1]
    assert cached[url2] == data[url2]
    assert "missing" not in cached

    # Update one
    new_vec1 = [0.9, 0.9]
    db.upsert_embeddings(session, {url1: json.dumps(new_vec1).encode("utf-8")}, backend)

    cached = db.get_embeddings(session, [url1], backend)
    assert cached[url1] == json.dumps(new_vec1).encode("utf-8")


def test_empty_database_operations_are_noops(session):
    assert db.init_engine(None) is None
    assert db.get_article(session, "missing") is None
    assert db.get_embeddings(session, [], "backend") == {}
    db.upsert_article(session, {})
    db.upsert_embeddings(session, {}, "backend")


def test_upsert_article_accepts_datetime_and_preserves_date_on_undated_update(session):
    published = datetime(2025, 1, 2, tzinfo=timezone.utc)
    url = "https://example.com/datetime"
    db.upsert_article(session, {"url": url})
    db.upsert_article(session, {"url": url, "published": published})
    db.upsert_article(session, {"url": url, "title": "updated"})

    cached = db.get_article(session, url)
    assert cached is not None
    assert cached["published"] == published.replace(tzinfo=None)


@pytest.mark.parametrize(
    "operation,args",
    [
        (db.upsert_article, ({"url": "https://example.com/fail"},)),
        (db.upsert_embeddings, ({"url": b"vector"}, "backend")),
    ],
)
def test_upsert_rolls_back_commit_failures(operation, args):
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    session.execute.return_value.scalars.return_value.all.return_value = []
    session.commit.side_effect = RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        operation(session, *args)

    session.rollback.assert_called_once_with()
