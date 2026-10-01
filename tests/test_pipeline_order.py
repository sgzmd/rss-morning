"""Tests for Phase 3 pipeline order: collect -> dedupe -> Jev classify -> extract."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from rss_morning.models import FeedConfig, FeedEntry
from rss_morning.runner import RunConfig, execute
from rss_morning.classify import ClassificationDecision, TypeSafeError


def _make_entry(url: str, title: str = "Title", category: str = "Sec") -> FeedEntry:
    return FeedEntry(
        link=url,
        title=title,
        category=category,
        summary=f"Summary for {title}",
        published=datetime.now(timezone.utc),
    )


def test_pipeline_order_metadata_reaches_jev_before_extraction(monkeypatch):
    """Verify feed metadata reaches Jev before any article extraction occurs."""
    entry_keep = _make_entry("https://ex.com/keep", "Keep Story")
    entry_drop = _make_entry("https://ex.com/drop", "Drop Story")

    call_order = []

    def fake_classify(metadata, **kwargs):
        call_order.append(("classify", metadata["link"]))
        is_keep = "keep" in metadata["link"]
        return ClassificationDecision(
            relevance_probability=0.9 if is_keep else 0.1,
            primary_area="corporate_it" if is_keep else "other",
            area_confidence=0.95,
            is_plausible=is_keep,
        )

    def fake_fetch_content(url, **kwargs):
        call_order.append(("extract", url))
        mock_content = MagicMock()
        mock_content.text = "Extracted body"
        mock_content.image = None
        return mock_content

    monkeypatch.setattr(
        "rss_morning.runner.parse_feeds_config", lambda p: [FeedConfig("C", "F", "u")]
    )
    monkeypatch.setattr(
        "rss_morning.runner.fetch_feed_entries", lambda f: [entry_keep, entry_drop]
    )
    monkeypatch.setattr("rss_morning.runner.classify_entry", fake_classify)
    monkeypatch.setattr("rss_morning.runner.fetch_article_content", fake_fetch_content)

    config = RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        classify=True,
    )
    result = execute(config)
    assert not result.is_summary

    # Classifications must happen before extraction
    classify_calls = [c for c in call_order if c[0] == "classify"]
    extract_calls = [c for c in call_order if c[0] == "extract"]

    assert len(classify_calls) == 2
    assert len(extract_calls) == 1
    assert extract_calls[0] == ("extract", "https://ex.com/keep")

    # The extract call must happen AFTER all classify calls
    assert call_order.index(extract_calls[0]) > call_order.index(classify_calls[-1])


def test_rejected_entries_are_not_extracted(monkeypatch):
    """Verify that articles rejected by Jev are never fetched via HTTP."""
    entry_drop = _make_entry("https://ex.com/irrelevant", "Irrelevant Story")

    mock_extract = MagicMock()
    mock_classify = MagicMock(
        return_value=ClassificationDecision(
            relevance_probability=0.1,
            primary_area="other",
            area_confidence=0.99,
            is_plausible=False,
        )
    )

    monkeypatch.setattr(
        "rss_morning.runner.parse_feeds_config", lambda p: [FeedConfig("C", "F", "u")]
    )
    monkeypatch.setattr("rss_morning.runner.fetch_feed_entries", lambda f: [entry_drop])
    monkeypatch.setattr("rss_morning.runner.classify_entry", mock_classify)
    monkeypatch.setattr("rss_morning.runner.fetch_article_content", mock_extract)

    config = RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        classify=True,
    )
    result = execute(config)

    assert mock_classify.call_count == 1
    mock_extract.assert_not_called()
    assert json.loads(result.output_text) == []


def test_accepted_entries_are_extracted(monkeypatch):
    """Verify that plausible entries are extracted and retain Jev primary_area."""
    entry_keep = _make_entry("https://ex.com/vuln", "Cisco Zero-Day")

    mock_content = MagicMock()
    mock_content.text = "Full article about Cisco SD-WAN"
    mock_content.image = "https://ex.com/img.png"

    mock_classify = MagicMock(
        return_value=ClassificationDecision(
            relevance_probability=0.88,
            primary_area="corporate_it",
            area_confidence=0.92,
            is_plausible=True,
        )
    )

    monkeypatch.setattr(
        "rss_morning.runner.parse_feeds_config", lambda p: [FeedConfig("C", "F", "u")]
    )
    monkeypatch.setattr("rss_morning.runner.fetch_feed_entries", lambda f: [entry_keep])
    monkeypatch.setattr("rss_morning.runner.classify_entry", mock_classify)
    monkeypatch.setattr(
        "rss_morning.runner.fetch_article_content", lambda u, **kw: mock_content
    )

    config = RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        classify=True,
    )
    result = execute(config)

    assert "Cisco SD-WAN" in result.output_text
    assert "corporate_it" in result.output_text


def test_extraction_failures_remain_isolated(monkeypatch):
    """Verify that an extraction failure on one candidate does not drop other candidates."""
    entry1 = _make_entry("https://ex.com/crash", "Crash Article")
    entry2 = _make_entry("https://ex.com/good", "Good Article")

    def fake_extract(url, **kwargs):
        if "crash" in url:
            raise ConnectionResetError("Connection lost")
        mock = MagicMock()
        mock.text = "Good text"
        mock.image = None
        return mock

    decision = ClassificationDecision(
        relevance_probability=0.8,
        primary_area="corporate_it",
        area_confidence=0.9,
        is_plausible=True,
    )

    monkeypatch.setattr(
        "rss_morning.runner.parse_feeds_config", lambda p: [FeedConfig("C", "F", "u")]
    )
    monkeypatch.setattr(
        "rss_morning.runner.fetch_feed_entries", lambda f: [entry1, entry2]
    )
    monkeypatch.setattr("rss_morning.runner.classify_entry", lambda m, **kw: decision)
    monkeypatch.setattr("rss_morning.runner.fetch_article_content", fake_extract)

    config = RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        classify=True,
    )
    result = execute(config)

    assert "Good Article" in result.output_text
    assert "Crash Article" not in result.output_text


def test_exact_duplicate_urls_deduplicated_before_jev(monkeypatch):
    """Verify duplicate URLs across feeds are deduplicated before Jev classification."""
    feed1 = FeedConfig("Feed1", "F1", "https://f1.example.com")
    feed2 = FeedConfig("Feed2", "F2", "https://f2.example.com")

    entry1 = _make_entry("https://shared.com/story", "Shared Story")
    entry2 = _make_entry("https://shared.com/story", "Shared Story Dup")

    def fake_fetch_feed(feed):
        return [entry1] if feed.url == "https://f1.example.com" else [entry2]

    mock_classify = MagicMock(
        return_value=ClassificationDecision(
            relevance_probability=0.9,
            primary_area="account_security",
            area_confidence=0.95,
            is_plausible=True,
        )
    )

    mock_extract = MagicMock()
    mock_extract.return_value = MagicMock(text="Content", image=None)

    monkeypatch.setattr(
        "rss_morning.runner.parse_feeds_config", lambda p: [feed1, feed2]
    )
    monkeypatch.setattr("rss_morning.runner.fetch_feed_entries", fake_fetch_feed)
    monkeypatch.setattr("rss_morning.runner.classify_entry", mock_classify)
    monkeypatch.setattr("rss_morning.runner.fetch_article_content", mock_extract)

    config = RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        classify=True,
    )
    result = execute(config)

    # Classify should be called only ONCE for the deduplicated URL
    assert mock_classify.call_count == 1
    assert mock_extract.call_count == 1
    assert len(json.loads(result.output_text)) == 1


def test_classifier_failure_behaviour_is_explicit(monkeypatch):
    """Verify that if TypeSafe Jev fails, execute() fails clearly without silent degradation."""
    entry = _make_entry("https://ex.com/article", "Test Article")

    def failing_classify(metadata, **kwargs):
        raise TypeSafeError("TypeSafe API HTTP 503: Service Unavailable")

    monkeypatch.setattr(
        "rss_morning.runner.parse_feeds_config", lambda p: [FeedConfig("C", "F", "u")]
    )
    monkeypatch.setattr("rss_morning.runner.fetch_feed_entries", lambda f: [entry])
    monkeypatch.setattr("rss_morning.runner.classify_entry", failing_classify)

    config = RunConfig(
        feeds_file="dummy.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        classify=True,
    )

    with pytest.raises(TypeSafeError, match="TypeSafe API HTTP 503"):
        execute(config)
