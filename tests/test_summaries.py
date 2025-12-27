import json
import os
import pytest
from unittest.mock import MagicMock, patch
from rss_morning import summaries
from rss_morning.config import TopicCluster


@pytest.fixture
def mock_genai_client():
    with patch("rss_morning.summaries.genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        # Ensure 'types' is also mocked
        with patch("rss_morning.summaries.types") as mock_types:
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "dummy-key"}):
                yield mock_client, mock_types


@pytest.fixture
def mock_topics_summaries():
    return [
        TopicCluster("T1", "Tech", ["keyword1"]),
        TopicCluster("T2", "Health", ["keyword2"]),
    ]


def test_generate_summary_groups_by_topic(mock_genai_client, mock_topics_summaries):
    mock_client, mock_types = mock_genai_client

    # Setup mock response
    # We expect one call per topic with articles.
    # Input articles: 2 for Tech, 1 for Health

    articles = [
        {"title": "Tech 1", "category": "Tech", "text": "Content 1"},
        {"title": "Tech 2", "category": "Tech", "text": "Content 2"},
        {"title": "Health 1", "category": "Health", "text": "Content 3"},
    ]

    # We need side_effect to return different valid JSONs
    def side_effect(model, contents, config):
        # check contents to guess topic? or just return generic
        # Contents is prompt string.
        # prompt = str(contents)

        return MagicMock(
            text=json.dumps(
                {
                    "valid_count": 5,
                    "key_threats_summary": ["Threat 1", "Threat 2"],
                    # We optionally return valid_article_ids
                    "valid_article_ids": [0, 1],  # Indices in the batch
                }
            )
        )

    mock_client.models.generate_content.side_effect = side_effect

    result_json = summaries.generate_summary(
        articles, "System Prompt", topics=mock_topics_summaries
    )
    result = json.loads(result_json)

    # Should have 2 items in summaries list
    items = result["summaries"]
    assert len(items) == 2

    tech_summary = next(i for i in items if i["topic"] == "Tech")
    assert tech_summary["valid_count"] == 5
    assert len(tech_summary["articles"]) == 2

    health_summary = next(i for i in items if i["topic"] == "Health")
    assert health_summary["key_threats"] == ["Threat 1", "Threat 2"]


def test_generate_summary_handles_empty_input():
    result = summaries.generate_summary([], "Prompt")
    assert json.loads(result) == {"summaries": []}


def test_generate_summary_skips_empty_topics(mock_genai_client, mock_topics_summaries):
    mock_client, _ = mock_genai_client

    # Only Tech articles
    articles = [
        {"title": "Tech 1", "category": "Tech", "text": "Content 1"},
    ]

    mock_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"valid_count": 1, "key_threats_summary": ["foo"]})
    )

    result_json = summaries.generate_summary(
        articles, "Prompt", topics=mock_topics_summaries
    )
    result = json.loads(result_json)

    items = result["summaries"]
    assert len(items) == 1
    assert items[0]["topic"] == "Tech"
    # Health should be skipped
