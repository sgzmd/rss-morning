"""Tests for the database abstraction layer."""

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
