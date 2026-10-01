import json
from unittest.mock import patch

import pytest

from rss_morning import summaries


@pytest.fixture
def mock_complete_structured():
    with patch("rss_morning.summaries.complete_structured") as mock_complete:
        yield mock_complete


def test_generate_summary_batching(mock_complete_structured):
    mock_complete_structured.return_value = {
        "summaries": [
            {
                "url": "http://example.com",
                "category": "Tech",
                "summary": {
                    "title": "T",
                    "rank-reasoning": "R",
                    "what": "W",
                    "so-what": "S",
                    "now-what": "N",
                },
            }
        ],
        "exec-summary": [],
    }

    articles = [
        {"url": f"http://example.com/{i}", "title": f"Title {i}"} for i in range(10)
    ]

    # Run with batch_size=2, so we expect 5 calls
    summaries.generate_summary(articles, "System Prompt", batch_size=2)

    assert mock_complete_structured.call_count == 5


def test_generate_summary_partial_failure(mock_complete_structured):
    # 3 batches of 1
    articles = [
        {"url": f"http://example.com/{i}", "title": f"Title {i}"} for i in range(3)
    ]

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("API Error")
        return {
            "summaries": [
                {
                    "url": "val",
                    "category": "val",
                    "summary": {
                        "title": "T",
                        "rank-reasoning": "R",
                        "what": "W",
                        "so-what": "S",
                        "now-what": "N",
                    },
                }
            ],
            "exec-summary": [],
        }

    mock_complete_structured.side_effect = side_effect

    result_json = summaries.generate_summary(articles, "System Prompt", batch_size=1)
    result = json.loads(result_json)

    # Should have 2 summaries (batch 0 and 2), batch 1 failed
    assert len(result["summaries"]) == 2


def test_generate_summary_empty_input():
    result = summaries.generate_summary([], "Prompt")
    assert json.loads(result) == {"summaries": []}


def test_generate_summary_extracts_exec_summary(mock_complete_structured):
    mock_complete_structured.return_value = {
        "exec-summary": ["- Point 1", "- Point 2"],
        "summaries": [
            {
                "url": "http://example.com/1",
                "category": "Tech",
                "summary": {
                    "title": "T",
                    "rank-reasoning": "R",
                    "what": "W",
                    "so-what": "S",
                    "now-what": "N",
                },
            }
        ],
    }

    articles = [{"url": "http://example.com/1", "title": "Title 1"}]
    result_json = summaries.generate_summary(articles, "System Prompt")
    result = json.loads(result_json)

    assert "exec_summary" in result
    assert result["exec_summary"] == "- Point 1\n- Point 2"


def test_generate_summary_dry_run(mock_complete_structured):
    articles = [{"url": "http://example.com/1", "title": "Title 1"}]

    with patch("rss_morning.summaries.logger") as mock_logger:
        result_json, result_dict = summaries.generate_summary(
            articles, "System Prompt", dry_run=True, return_dict=True
        )

        assert result_dict.get("dry_run") is True

        logs = [str(args[0]) for args, _ in mock_logger.info.call_args_list]
        assert any("DRY RUN: Prepared payload" in log for log in logs)
        assert any("DRY RUN: skipping API call" in log for log in logs)

        mock_complete_structured.assert_not_called()
