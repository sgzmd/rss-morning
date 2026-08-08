import json
import logging
import os
import pytest
from unittest.mock import MagicMock, patch
from rss_morning import summaries


@pytest.fixture
def mock_genai_client():
    with patch("rss_morning.summaries.genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        # Ensure 'types' is also mocked as it's used for config
        with patch("rss_morning.summaries.types") as mock_types:
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "dummy-key"}):
                yield mock_client, mock_types


def test_generate_summary_batching(mock_genai_client):
    mock_client, mock_types = mock_genai_client

    # Setup mock response chunks
    def side_effect(model, contents, config):
        # We can inspect 'contents' to see which articles are in this batch if needed
        # For now, just return a generic valid JSON response structure
        yield MagicMock(
            text=json.dumps(
                {
                    "summaries": [
                        {
                            "url": "http://example.com",
                            "category": "Tech",
                            "summary": {
                                "title": "T",
                                "what": "W",
                                "so-what": "S",
                                "now-what": "N",
                            },
                        }
                    ]
                }
            )
        )

    mock_client.models.generate_content_stream.side_effect = side_effect

    articles = [
        {"url": f"http://example.com/{i}", "title": f"Title {i}"} for i in range(10)
    ]

    # Run with batch_size=2, so we expect 5 calls
    summaries.generate_summary(articles, "System Prompt", batch_size=2)

    assert mock_client.models.generate_content_stream.call_count == 5


def test_generate_summary_partial_failure(mock_genai_client):
    mock_client, mock_types = mock_genai_client

    # 3 batches of 1
    articles = [
        {"url": f"http://example.com/{i}", "title": f"Title {i}"} for i in range(3)
    ]

    # Fail the second batch
    success_response = MagicMock(
        text=json.dumps(
            {
                "summaries": [
                    {
                        "url": "val",
                        "category": "val",
                        "summary": {
                            "title": "T",
                            "what": "W",
                            "so-what": "S",
                            "now-what": "N",
                        },
                    }
                ]
            }
        )
    )

    call_count = 0

    def side_effect(model, contents, config):
        nonlocal call_count
        call_count += 1
        # Generator that simulates the response stream
        if call_count == 2:
            raise RuntimeError("API Error")
        yield success_response

    mock_client.models.generate_content_stream.side_effect = side_effect

    result_json = summaries.generate_summary(articles, "System Prompt", batch_size=1)
    result = json.loads(result_json)

    # Should have 2 summaries (batch 0 and 2), batch 1 failed
    assert len(result["summaries"]) == 2


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
    mock_client, _ = mock_genai_client
    mock_client.models.generate_content_stream.return_value = [
        MagicMock(text=response_text)
    ]

    result = summaries.generate_summary([{"title": "Title"}], "System Prompt")

    assert json.loads(result) == {"summaries": []}


@pytest.mark.parametrize("error", [TimeoutError("timeout"), RuntimeError("rate limit")])
def test_generate_summary_omits_provider_error(mock_genai_client, error):
    mock_client, _ = mock_genai_client
    mock_client.models.generate_content_stream.side_effect = error

    result = summaries.generate_summary([{"title": "Title"}], "System Prompt")

    assert json.loads(result) == {"summaries": []}


def test_generate_summary_empty_input():
    result = summaries.generate_summary([], "Prompt")
    assert json.loads(result) == {"summaries": []}


def test_generate_summary_logging(mock_genai_client):
    mock_client, mock_types = mock_genai_client

    # Setup mock response
    mock_client.models.generate_content_stream.return_value = [
        MagicMock(text=json.dumps({"summaries": []}))
    ]

    articles = [{"url": "http://example.com/1", "title": "Title 1"}]

    with patch("rss_morning.summaries.logger") as mock_logger:
        summaries.generate_summary(articles, "System Prompt")

        # Check if debug was called
        # We expect at least two calls: one for request, one for response
        assert mock_logger.debug.call_count >= 2

        # Verify request logging - getting the actual call arguments might be verbose
        # so just checking if we logged something that looks like our payload
        requests_calls = [args[0] for args, _ in mock_logger.debug.call_args_list]
        assert any("Gemini request payload: %s" in str(arg) for arg in requests_calls)
        assert any("Gemini response text: %s" in str(arg) for arg in requests_calls)


def test_generate_summary_does_not_log_api_key(mock_genai_client, caplog):
    caplog.set_level(logging.INFO, logger="rss_morning.summaries")
    mock_client, _ = mock_genai_client
    mock_client.models.generate_content_stream.return_value = [
        MagicMock(text=json.dumps({"summaries": []}))
    ]

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "highly-secret-api-key"}):
        summaries.generate_summary([{"title": "Title"}], "System Prompt")

    assert "highly-secret-api-key" not in caplog.text


def test_generate_summary_extracts_exec_summary(mock_genai_client):
    mock_client, mock_types = mock_genai_client

    # Mock response with exec-summary
    mock_client.models.generate_content_stream.return_value = [
        MagicMock(
            text=json.dumps(
                {
                    "exec-summary": ["- Point 1", "- Point 2"],
                    "summaries": [
                        {
                            "url": "http://example.com/1",
                            "category": "Tech",
                            "summary": {
                                "title": "T",
                                "what": "W",
                                "so-what": "S",
                                "now-what": "N",
                            },
                        }
                    ],
                }
            )
        )
    ]

    articles = [{"url": "http://example.com/1", "title": "Title 1"}]
    result_json = summaries.generate_summary(articles, "System Prompt")
    result = json.loads(result_json)

    assert "exec_summary" in result
    assert result["exec_summary"] == "- Point 1\n- Point 2"


def test_generate_summary_dry_run(mock_genai_client):
    mock_client, _ = mock_genai_client
    articles = [{"url": "http://example.com/1", "title": "Title 1"}]

    with patch("rss_morning.summaries.logger") as mock_logger:
        result_json, result_dict = summaries.generate_summary(
            articles, "System Prompt", dry_run=True, return_dict=True
        )

        # Should return mock response
        assert result_dict.get("dry_run") is True

        # Should verify logs indicate dry run
        logs = [str(args[0]) for args, _ in mock_logger.info.call_args_list]
        assert any("DRY RUN: Prepared payload" in log for log in logs)
        assert any("DRY RUN: skipping API call" in log for log in logs)

        # Should NOT have called the API
        mock_client.models.generate_content_stream.assert_not_called()


def test_generate_summary_dry_run_does_not_log_article_content_at_info(
    mock_genai_client, caplog
):
    mock_client, _ = mock_genai_client
    caplog.set_level(logging.INFO, logger="rss_morning.summaries")

    summaries.generate_summary(
        [{"title": "private-title", "text": "private-article-body"}],
        "private-system-prompt",
        dry_run=True,
    )

    assert "private-title" not in caplog.text
    assert "private-article-body" not in caplog.text
    assert "private-system-prompt" not in caplog.text
    mock_client.models.generate_content_stream.assert_not_called()
