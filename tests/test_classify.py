"""Unit tests for TypeSafe Jev classification."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from rss_morning.classify import (
    classify_entry,
    classify_entries,
    TypeSafeError,
    TYPESAFE_API_URL,
    DEFAULT_MODEL,
    DEFAULT_RELEVANCE_THRESHOLD,
    ClassificationDecision,
)


def test_classify_missing_api_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(
        TypeSafeError, match="TYPESAFE_API_KEY environment variable is not set"
    ):
        classify_entry({"title": "Test Title"})


def test_classify_request_state_and_questions(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-secret-key-123456")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "jev-latest",
        "answers": {
            "is_relevant": {"type": "noul", "noul": 0.85},
            "primary_area": {
                "type": "choice",
                "choice": "corporate_it",
                "confidence": 0.92,
                "probabilities": {"corporate_it": 0.92, "other": 0.08},
            },
        },
    }

    entry = {
        "title": "Cisco SD-WAN Zero-Day Exploit",
        "summary": "Actively exploited vulnerability in Catalyst SD-WAN Manager.",
        "category": "Networking",
        "url": "https://example.com/cisco",
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        decision = classify_entry(entry)

        assert decision.is_plausible is True
        assert decision.relevance_probability == 0.85
        assert decision.primary_area == "corporate_it"
        assert decision.area_confidence == 0.92

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert url == TYPESAFE_API_URL

        kwargs = mock_post.call_args[1]
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer ts-secret-key-123456"
        assert headers["Content-Type"] == "application/json"

        payload = kwargs["json"]
        assert payload["model"] == DEFAULT_MODEL

        # Verify state contains cheap metadata only
        state = payload["state"]
        assert state["title"] == "Cisco SD-WAN Zero-Day Exploit"
        assert (
            state["summary"]
            == "Actively exploited vulnerability in Catalyst SD-WAN Manager."
        )
        assert "text" not in state
        assert "content" not in state

        # Verify questions structure
        questions = payload["questions"]
        assert questions["is_relevant"]["type"] == "noul"
        assert "instructions" in questions["is_relevant"]
        assert "criteria" in questions["is_relevant"]

        assert questions["primary_area"]["type"] == "choice"
        assert "corporate_security" in questions["primary_area"]["criteria"]
        assert "other" in questions["primary_area"]["criteria"]


def test_classify_response_parsing_drop(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": {
            "is_relevant": {"type": "noul", "noul": 0.15},
            "primary_area": {
                "type": "choice",
                "choice": "other",
                "confidence": 0.98,
            },
        }
    }

    entry = {
        "title": "Power outage in Kowloon",
        "summary": "Substation maintenance failure.",
    }

    with patch("requests.post", return_value=mock_resp):
        decision = classify_entry(entry, threshold=DEFAULT_RELEVANCE_THRESHOLD)
        assert decision.is_plausible is False
        assert decision.relevance_probability == 0.15
        assert decision.primary_area == "other"


def test_classify_api_failure(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_resp.json.side_effect = ValueError("No JSON")

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(TypeSafeError, match="TypeSafe API returned HTTP 500"):
            classify_entry({"title": "Test"})


def test_classify_timeout(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    with patch("requests.post", side_effect=requests.Timeout("Request timed out")):
        with pytest.raises(TypeSafeError, match="TypeSafe API request timed out"):
            classify_entry({"title": "Test"}, timeout=2.0)


def test_classify_malformed_response(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"incomplete": {}}

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(
            TypeSafeError, match="Failed to parse TypeSafe response payload"
        ):
            classify_entry({"title": "Test"})


def test_classify_no_secret_logging(caplog, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-secret-key-super-confidential-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": {
            "is_relevant": {"type": "noul", "noul": 0.9},
            "primary_area": {
                "type": "choice",
                "choice": "account_security",
                "confidence": 0.8,
            },
        }
    }

    with caplog.at_level(logging.DEBUG):
        with patch("requests.post", return_value=mock_resp):
            classify_entry({"title": "Account test"})

    assert "ts-secret-key-super-confidential-token" not in caplog.text
    assert "...oken" in caplog.text


def test_classify_entries_sequence(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    mock_decision = ClassificationDecision(
        relevance_probability=0.75,
        primary_area="ai_security",
        area_confidence=0.88,
        is_plausible=True,
    )

    with patch("rss_morning.classify.classify_entry", return_value=mock_decision):
        entries = [{"title": "Item 1"}, {"title": "Item 2"}]
        results = classify_entries(entries)

        assert len(results) == 2
        assert results[0][0]["title"] == "Item 1"
        assert results[0][1] == mock_decision


def test_eval_fixtures_data_structure():
    """Verify that tests/fixtures/eval_articles.json contains valid evaluation candidates."""
    fixture_path = Path(__file__).parent / "fixtures" / "eval_articles.json"
    assert fixture_path.exists()

    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) >= 8
    keep_count = sum(1 for item in data if item["expected_decision"] == "KEEP")
    drop_count = sum(1 for item in data if item["expected_decision"] == "DROP")
    maybe_count = sum(1 for item in data if item["expected_decision"] == "MAYBE")

    assert keep_count >= 3
    assert drop_count >= 3
    assert maybe_count >= 1


def test_classify_asymmetric_area_thresholds(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")

    from rss_morning.models import AreaConfig

    areas = {
        "mobile_security": AreaConfig(
            key="mobile_security",
            label="Mobile Security",
            description="Mobile security",
            threshold=0.40,
        ),
        "other": AreaConfig(
            key="other",
            label="Other",
            description="Other",
            threshold=0.75,
        ),
    }

    # Case 1: mobile_security with prob 0.42 >= 0.40 threshold -> KEEP
    resp_mobile = MagicMock()
    resp_mobile.status_code = 200
    resp_mobile.json.return_value = {
        "answers": {
            "is_relevant": {"type": "noul", "noul": 0.42},
            "primary_area": {
                "type": "choice",
                "choice": "mobile_security",
                "confidence": 0.90,
            },
        }
    }

    with patch("requests.post", return_value=resp_mobile):
        decision = classify_entry(
            {"title": "iOS Exploit"},
            areas=areas,
        )
        assert decision.is_plausible is True
        assert decision.effective_threshold == 0.40
        assert decision.primary_area == "mobile_security"

    # Case 2: other with prob 0.60 < 0.75 threshold -> DROP
    resp_other = MagicMock()
    resp_other.status_code = 200
    resp_other.json.return_value = {
        "answers": {
            "is_relevant": {"type": "noul", "noul": 0.60},
            "primary_area": {
                "type": "choice",
                "choice": "other",
                "confidence": 0.85,
            },
        }
    }

    with patch("requests.post", return_value=resp_other):
        decision = classify_entry(
            {"title": "Routine CVE Patch"},
            areas=areas,
        )
        assert decision.is_plausible is False
        assert decision.effective_threshold == 0.75
        assert decision.primary_area == "other"
