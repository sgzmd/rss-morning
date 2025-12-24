"""Tests for the database abstraction layer."""

import json

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
