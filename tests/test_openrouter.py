"""Unit tests for the OpenRouter client helper."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from rss_morning.openrouter import (
    complete_structured,
    OpenRouterError,
    OPENROUTER_API_URL,
)


def test_openrouter_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(
        OpenRouterError, match="OPENROUTER_API_KEY environment variable is not set"
    ):
        complete_structured(
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"type": "object"},
            schema_name="test",
        )


def test_openrouter_auth_header_and_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-secretkey123456")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"key": "value"}'}}]
    }

    schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
        "additionalProperties": False,
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = complete_structured(
            messages=[{"role": "user", "content": "hello"}],
            json_schema=schema,
            schema_name="my_schema",
            model="openai/gpt-4o-mini",
        )

        assert result == {"key": "value"}
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert url == OPENROUTER_API_URL

        kwargs = mock_post.call_args[1]
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-or-v1-secretkey123456"
        assert headers["Content-Type"] == "application/json"

        payload = kwargs["json"]
        assert payload["model"] == "openai/gpt-4o-mini"
        assert payload["provider"] == {"require_parameters": True}
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["response_format"]["json_schema"]["name"] == "my_schema"


def test_openrouter_malformed_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "not-valid-json{"}}]
    }

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(
            OpenRouterError, match="Failed to parse structured response"
        ):
            complete_structured(
                messages=[{"role": "user", "content": "hi"}],
                json_schema={"type": "object"},
                schema_name="test",
            )


def test_openrouter_http_failure(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {
        "error": {"message": "No available provider meets requirements"}
    }

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(
            OpenRouterError, match="OpenRouter returned HTTP 503: No available provider"
        ):
            complete_structured(
                messages=[{"role": "user", "content": "hi"}],
                json_schema={"type": "object"},
                schema_name="test",
            )


def test_openrouter_timeout(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch("requests.post", side_effect=requests.Timeout("Connection timed out")):
        with pytest.raises(OpenRouterError, match="OpenRouter request timed out"):
            complete_structured(
                messages=[{"role": "user", "content": "hi"}],
                json_schema={"type": "object"},
                schema_name="test",
                timeout=5.0,
            )


def test_openrouter_no_secret_logging(caplog, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-my-very-secret-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }

    with caplog.at_level(logging.DEBUG):
        with patch("requests.post", return_value=mock_resp):
            complete_structured(
                messages=[{"role": "user", "content": "hi"}],
                json_schema={"type": "object"},
                schema_name="test",
            )

    assert "sk-or-v1-my-very-secret-token" not in caplog.text
    assert "...oken" in caplog.text


def test_openrouter_default_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }
    with patch("requests.post", return_value=mock_resp) as mock_post:
        complete_structured(
            messages=[{"role": "user", "content": "hi"}],
            json_schema={"type": "object"},
            schema_name="test",
        )
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "google/gemini-3.8-flash"
