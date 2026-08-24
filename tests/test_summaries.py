import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rss_morning import summaries


@pytest.fixture
def mock_genai_client():
    with patch("rss_morning.summaries.genai") as mock_genai:
        client = MagicMock()
        mock_genai.Client.return_value = client
        with patch("rss_morning.summaries.types") as mock_types:
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "dummy-key"}):
                yield client, mock_types


def _article(number=1):
    return {
        "url": f"https://example.com/{number}",
        "category": "Tech",
        "title": f"Title {number}",
    }


def _payload(articles, *, exec_summary=None):
    return {
        "exec-summary": exec_summary or [],
        "summaries": [
            {
                "url": article["url"],
                "category": "model-category",
                "summary": {
                    "title": "<b>Title</b>",
                    "rank-reasoning": "<i>Relevant</i>",
                    "what": "<i>What</i>",
                    "so-what": "<i>Why</i>",
                    "now-what": "<i>Act</i>",
                },
            }
            for article in articles
        ],
    }


def test_generate_summary_gemini_batching_and_timeout(mock_genai_client):
    client, types_mock = mock_genai_client
    articles = [_article(i) for i in range(10)]
    batches = iter([articles[index : index + 2] for index in range(0, 10, 2)])
    client.models.generate_content_stream.side_effect = lambda **_kwargs: [
        SimpleNamespace(text=None),
        SimpleNamespace(text=json.dumps(_payload(next(batches)))),
    ]

    result = summaries.generate_summary(
        articles, "System Prompt", batch_size=2, provider="gemini"
    )

    assert len(json.loads(result)["summaries"]) == 10
    assert client.models.generate_content_stream.call_count == 5
    assert types_mock.HttpOptions.call_args.kwargs["timeout"] == 90_000


@pytest.mark.parametrize(
    "response_text",
    [
        "unexpected prose",
        "[]",
        json.dumps({"summaries": "not-a-list"}),
        json.dumps({"summaries": [{"summary": "not-an-object"}]}),
        json.dumps({"summaries": [], "exec-summary": "not-a-list"}),
        json.dumps({"summaries": [], "exec-summary": [1]}),
    ],
)
def test_generate_summary_omits_malformed_batch(mock_genai_client, response_text):
    client, _ = mock_genai_client
    client.models.generate_content_stream.return_value = [
        SimpleNamespace(text=response_text)
    ]
    result = summaries.generate_summary(
        [_article()], "System Prompt", provider="gemini", sleeper=lambda _: None
    )
    assert json.loads(result) == {"summaries": []}


def test_generate_summary_empty_input_and_return_dict():
    assert json.loads(summaries.generate_summary([], "Prompt")) == {"summaries": []}
    rendered, result = summaries.generate_summary([], "Prompt", return_dict=True)
    assert json.loads(rendered) == result == {"summaries": []}


def test_sanitize_html_handles_empty_text():
    assert summaries.sanitize_html("") == ""


def test_generate_summary_rejects_provider_and_missing_credentials(monkeypatch):
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        summaries.generate_summary([_article()], "Prompt", provider="other")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        summaries.generate_summary([_article()], "Prompt")

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        summaries.generate_summary([_article()], "Prompt", provider="gemini")


def test_generate_summary_requires_gemini_dependency(monkeypatch):
    monkeypatch.setattr(summaries, "genai", None)
    with pytest.raises(RuntimeError, match="google-genai package"):
        summaries.generate_summary([_article()], "Prompt", provider="gemini")


def test_generate_summary_logs_metadata_not_secrets_or_content(
    mock_genai_client, caplog
):
    client, _ = mock_genai_client
    client.models.generate_content_stream.return_value = [
        SimpleNamespace(text=json.dumps(_payload([_article()])))
    ]
    caplog.set_level(logging.INFO, logger="rss_morning.summaries")
    with patch.dict(os.environ, {"GOOGLE_API_KEY": "highly-secret-api-key"}):
        summaries.generate_summary(
            [{**_article(), "text": "private-article-body"}],
            "private-system-prompt",
            provider="gemini",
        )
    assert "highly-secret-api-key" not in caplog.text
    assert "private-article-body" not in caplog.text
    assert "private-system-prompt" not in caplog.text


def test_generate_summary_extracts_exec_summary_and_sanitizes(mock_genai_client):
    client, _ = mock_genai_client
    client.models.generate_content_stream.return_value = [
        SimpleNamespace(
            text=json.dumps(_payload([_article()], exec_summary=["<b>Point</b>"]))
        )
    ]
    rendered, result = summaries.generate_summary(
        [_article()], "Prompt", provider="gemini", return_dict=True
    )
    assert json.loads(rendered) == result
    assert result["exec_summary"] == "Point"
    assert result["summaries"][0]["category"] == "Tech"
    assert result["summaries"][0]["summary"] == {
        "title": "Title",
        "rank-reasoning": "Relevant",
        "what": "What",
        "so-what": "Why",
        "now-what": "Act",
    }


def test_generate_summary_dry_run_uses_no_provider_or_cache(mock_genai_client):
    client, _ = mock_genai_client
    result, parsed = summaries.generate_summary(
        [_article()], "Prompt", provider="gemini", dry_run=True, return_dict=True
    )
    assert json.loads(result) == parsed == {"dry_run": True}
    client.models.generate_content_stream.assert_not_called()


def test_generate_summary_uses_openrouter_structured_outputs(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="vendor/model",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(_payload([_article()]))
                        )
                    )
                ],
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(summaries, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    result = summaries.generate_summary(
        [_article()], "Prompt", provider="openrouter", model="vendor/model"
    )
    assert json.loads(result)["summaries"][0]["url"] == _article()["url"]
    assert captured["client"] == {
        "base_url": summaries.OPENROUTER_BASE_URL,
        "api_key": "secret",
    }
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["extra_body"]["provider"]["require_parameters"] is True
    assert captured["extra_body"]["provider"]["max_price"] == {
        "prompt": 0.2,
        "completion": 0.75,
    }
