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


def _payload(articles):
    return {
        "summaries": [
            {
                "url": article["url"],
                "relevant": True,
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
    responses = iter(
        [json.dumps(_payload(articles[index : index + 2])) for index in range(0, 10, 2)]
        + [json.dumps({"executive-summary": "One coherent briefing."})]
    )
    client.models.generate_content_stream.side_effect = lambda **_kwargs: [
        SimpleNamespace(text=None),
        SimpleNamespace(text=next(responses)),
    ]

    result = summaries.generate_summary(
        articles, "System Prompt", batch_size=2, provider="gemini"
    )

    assert len(json.loads(result)["summaries"]) == 10
    assert json.loads(result)["exec_summary"] == "One coherent briefing."
    assert client.models.generate_content_stream.call_count == 6
    assert types_mock.HttpOptions.call_args.kwargs["timeout"] == 90_000


@pytest.mark.parametrize(
    "response_text",
    [
        "unexpected prose",
        "[]",
        json.dumps({"summaries": "not-a-list"}),
        json.dumps({"summaries": [{"summary": "not-an-object"}]}),
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
    responses = iter(
        [
            json.dumps(_payload([_article()])),
            json.dumps({"executive-summary": "Private synthesis"}),
        ]
    )
    client.models.generate_content_stream.side_effect = lambda **_kwargs: [
        SimpleNamespace(text=next(responses))
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


def test_generate_summary_synthesizes_once_and_sanitizes(mock_genai_client):
    client, _ = mock_genai_client
    responses = iter(
        [
            json.dumps(_payload([_article()])),
            json.dumps({"executive-summary": "<b>One coherent point</b>"}),
        ]
    )
    client.models.generate_content_stream.side_effect = lambda **_kwargs: [
        SimpleNamespace(text=next(responses))
    ]
    rendered, result = summaries.generate_summary(
        [_article()], "Prompt", provider="gemini", return_dict=True
    )
    assert json.loads(rendered) == result
    assert result["exec_summary"] == "One coherent point"
    assert result["summaries"][0]["category"] == "Tech"
    assert result["summaries"][0]["summary"] == {
        "title": "Title",
        "rank-reasoning": "Relevant",
        "what": "What",
        "so-what": "Why",
        "now-what": "Act",
    }


def test_irrelevant_decision_is_excluded_before_executive_synthesis():
    article = _article()
    payload = _payload([article])
    item = payload["summaries"][0]
    item["relevant"] = False
    item["category"] = ""
    item["summary"] = {field: "" for field in summaries.REQUIRED_SUMMARY_FIELDS}
    executive = MagicMock()

    rendered, result = summaries.generate_summary(
        [article],
        "Prompt",
        return_dict=True,
        settings=summaries.SummarySettings(model="fixture/model"),
        provider_request=lambda *_args: summaries.ProviderResponse(
            json.dumps(payload), "fixture/model"
        ),
        executive_request=executive,
    )

    assert json.loads(rendered) == result == {"summaries": []}
    executive.assert_not_called()


def test_relevance_decision_is_required_for_every_submitted_url():
    article = _article()
    payload = _payload([article])
    del payload["summaries"][0]["relevant"]

    with pytest.raises(
        summaries.ResponseValidationError,
        match="relevance decision",
    ):
        summaries.reconcile_response(payload, [article])


def test_relevance_decision_requires_schema_fields_even_when_excluded():
    article = _article()
    invalid_category = _payload([article])
    invalid_category["summaries"][0]["category"] = None

    with pytest.raises(summaries.ResponseValidationError, match="category"):
        summaries.reconcile_response(invalid_category, [article])

    invalid_summary = _payload([article])
    invalid_summary["summaries"][0]["relevant"] = False
    invalid_summary["summaries"][0]["summary"]["what"] = None

    with pytest.raises(summaries.ResponseValidationError, match="invalid summary"):
        summaries.reconcile_response(invalid_summary, [article])


def test_generate_summary_dry_run_uses_no_provider_or_cache(mock_genai_client):
    client, _ = mock_genai_client
    result, parsed = summaries.generate_summary(
        [_article()], "Prompt", provider="gemini", dry_run=True, return_dict=True
    )
    assert json.loads(result) == parsed == {"dry_run": True}
    client.models.generate_content_stream.assert_not_called()


def test_generate_summary_uses_openrouter_structured_outputs(monkeypatch):
    captured = {"requests": []}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["requests"].append(kwargs)
            name = kwargs["response_format"]["json_schema"]["name"]
            content = (
                json.dumps({"executive-summary": "Coherent briefing"})
                if name == "rss_morning_executive_summary"
                else json.dumps(_payload([_article()]))
            )
            return SimpleNamespace(
                model="vendor/model",
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
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
    assert json.loads(result)["exec_summary"] == "Coherent briefing"
    assert captured["client"] == {
        "base_url": summaries.OPENROUTER_BASE_URL,
        "api_key": "secret",
    }
    assert [
        request["response_format"]["json_schema"]["name"]
        for request in captured["requests"]
    ] == ["rss_morning_summary", "rss_morning_executive_summary"]
    assert all(
        request["response_format"]["json_schema"]["strict"] is True
        for request in captured["requests"]
    )
    assert (
        captured["requests"][0]["extra_body"]["provider"]["require_parameters"] is True
    )
    assert captured["requests"][0]["extra_body"]["provider"]["max_price"] == {
        "prompt": 0.2,
        "completion": 0.75,
    }
