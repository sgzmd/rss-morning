"""Tests for Phase 4: Single editorial digest generation via OpenRouter."""

import json
from unittest.mock import patch

import pytest

from rss_morning.digest import (
    DEFAULT_EDITORIAL_PROMPT,
    EDITION_SCHEMA,
    build_editorial_input,
    generate_digest,
)


@pytest.fixture
def mock_complete_structured():
    with patch("rss_morning.digest.complete_structured") as mock_complete:
        yield mock_complete


def test_generate_digest_single_call_no_batching(mock_complete_structured):
    """Verify that generate_digest makes exactly ONE call regardless of article count."""
    mock_complete_structured.return_value = {
        "overview": "Overall calm day with one zero-day disclosure.",
        "attention": [
            {
                "title": "Critical VPN Auth Bypass",
                "summary": "Vendor disclosed auth bypass actively exploited in the wild.",
                "source_urls": ["https://example.com/vpn-1"],
                "primary_area": "corporate_it",
                "urgency_rationale": "Active in-the-wild exploitation confirmed by CISA.",
            }
        ],
        "watch": [
            {
                "title": "Routine Chrome Patch",
                "summary": "Google released desktop updates fixing memory issues.",
                "source_urls": ["https://example.com/chrome-1"],
                "primary_area": "corporate_it",
            }
        ],
    }

    # 15 candidate articles passed in
    articles = [
        {
            "url": f"https://example.com/{i}",
            "title": f"Story {i}",
            "text": f"Article body {i}",
            "summary": f"Summary {i}",
            "primary_area": "corporate_it",
        }
        for i in range(15)
    ]

    result = generate_digest(articles, system_prompt=None, model="test/model-v1")

    # MUST be exactly 1 call — no batching loops
    assert mock_complete_structured.call_count == 1
    call_args = mock_complete_structured.call_args[1]
    assert call_args["model"] == "test/model-v1"
    assert call_args["schema_name"] == "editorial_edition"
    assert call_args["json_schema"] == EDITION_SCHEMA

    assert result["overview"] == "Overall calm day with one zero-day disclosure."
    assert len(result["attention"]) == 1
    assert len(result["watch"]) == 1


def test_generate_digest_empty_input(mock_complete_structured):
    """Verify that empty article list short-circuits without calling OpenRouter."""
    result = generate_digest([])
    assert mock_complete_structured.call_count == 0
    assert result == {
        "overview": "No articles retrieved for this edition.",
        "attention": [],
        "watch": [],
    }


def test_generate_digest_dry_run(mock_complete_structured):
    """Verify that dry_run logs and returns placeholder without API calls."""
    articles = [{"url": "https://example.com/1", "title": "Article 1"}]
    result = generate_digest(articles, dry_run=True)
    assert mock_complete_structured.call_count == 0
    assert "Dry run overview" in result["overview"]
    assert result["attention"] == []
    assert result["watch"] == []


def test_editorial_schema_has_strict_structure():
    """Verify that EDITION_SCHEMA enforces required keys and forbids additional properties."""
    assert EDITION_SCHEMA["type"] == "object"
    assert EDITION_SCHEMA["additionalProperties"] is False
    assert set(EDITION_SCHEMA["required"]) == {"overview", "attention", "watch"}

    attention_schema = EDITION_SCHEMA["properties"]["attention"]["items"]
    assert attention_schema["additionalProperties"] is False
    assert set(attention_schema["required"]) == {
        "title",
        "summary",
        "source_urls",
        "primary_area",
        "urgency_rationale",
    }

    watch_schema = EDITION_SCHEMA["properties"]["watch"]["items"]
    assert watch_schema["additionalProperties"] is False
    assert set(watch_schema["required"]) == {
        "title",
        "summary",
        "source_urls",
        "primary_area",
    }


def test_grounding_and_anti_hallucination_guardrails_in_prompt():
    """Verify that prompt contains strict grounding and bans internal-environment claims."""
    prompt = DEFAULT_EDITORIAL_PROMPT.lower()
    assert "grounding" in prompt
    assert "ban internal-environment claims" in prompt
    assert "never state, assume, or imply that our organization" in prompt
    assert "preserve uncertainty" in prompt
    assert "collapse duplicates" in prompt
    assert "overview" in prompt
    assert "attention" in prompt
    assert "watch" in prompt


def test_collapse_duplicates_synthetic_fixture(mock_complete_structured):
    """Verify that duplicate stories covering the same event are collapsed with multiple source URLs."""
    # Two articles covering the exact same Citrix/NetScaler event from different outlets
    articles = [
        {
            "url": "https://bleepingcomputer.com/citrix-bleep",
            "title": "Citrix NetScaler Flaw Actively Exploited in Attacks",
            "text": "Citrix has released emergency security updates for NetScaler ADC and Gateway.",
            "primary_area": "corporate_it",
        },
        {
            "url": "https://thehackernews.com/netscaler-hack",
            "title": "Hackers Actively Targeting Citrix NetScaler Vulnerability",
            "text": "A critical vulnerability in Citrix Gateway is being exploited in the wild.",
            "primary_area": "corporate_it",
        },
    ]

    # Synthesized collapsed response from OpenRouter
    mock_complete_structured.return_value = {
        "overview": "Threat actors are actively targeting network perimeter appliances.",
        "attention": [
            {
                "title": "Citrix NetScaler Gateway Zero-Day Active Exploitation",
                "summary": "Emergency updates address an actively exploited vulnerability in NetScaler ADC and Gateway appliances.",
                "source_urls": [
                    "https://bleepingcomputer.com/citrix-bleep",
                    "https://thehackernews.com/netscaler-hack",
                ],
                "primary_area": "corporate_it",
                "urgency_rationale": "Active in-the-wild exploitation observed against perimeter appliances.",
            }
        ],
        "watch": [],
    }

    result = generate_digest(articles)

    assert len(result["attention"]) == 1
    # Both sources are preserved in single collapsed story
    assert len(result["attention"][0]["source_urls"]) == 2
    assert (
        "https://bleepingcomputer.com/citrix-bleep"
        in result["attention"][0]["source_urls"]
    )
    assert (
        "https://thehackernews.com/netscaler-hack"
        in result["attention"][0]["source_urls"]
    )


def test_build_editorial_input_formats_articles():
    """Verify build_editorial_input serializes candidates with metadata and classifications."""
    articles = [
        {
            "url": "https://example.com/test",
            "title": "Test Title",
            "summary": "Feed summary",
            "text": "Extracted full text content",
            "primary_area": "ai_security",
        }
    ]
    formatted_json = build_editorial_input(articles)
    data = json.loads(formatted_json)

    assert len(data) == 1
    assert data[0]["id"] == "art-1"
    assert data[0]["url"] == "https://example.com/test"
    assert data[0]["primary_area"] == "ai_security"
    assert data[0]["content"] == "Extracted full text content"


def test_build_edition_schema_dynamic_enum():
    from rss_morning.digest import build_edition_schema

    schema = build_edition_schema(["mobile_security", "ai_security"])
    att_area = schema["properties"]["attention"]["items"]["properties"]["primary_area"]
    watch_area = schema["properties"]["watch"]["items"]["properties"]["primary_area"]

    assert att_area["enum"] == ["mobile_security", "ai_security"]
    assert watch_area["enum"] == ["mobile_security", "ai_security"]


def test_generate_digest_with_areas_updates_prompt_and_schema(
    mock_complete_structured,
):
    from rss_morning.models import AreaConfig

    areas = {
        "mobile_security": AreaConfig(
            key="mobile_security",
            label="Mobile Security",
            description="Mobile bugs",
            threshold=0.40,
        ),
        "corporate_security": AreaConfig(
            key="corporate_security",
            label="Corporate Security",
            description="Corporate IT",
            threshold=0.45,
        ),
    }

    mock_complete_structured.return_value = {
        "overview": "Overview text",
        "attention": [],
        "watch": [],
    }

    articles = [{"url": "https://example.com/1", "title": "Art 1"}]
    generate_digest(articles, areas=areas)

    assert mock_complete_structured.call_count == 1
    call_args = mock_complete_structured.call_args[1]

    # Verify dynamic schema used
    schema = call_args["json_schema"]
    assert schema["properties"]["attention"]["items"]["properties"]["primary_area"][
        "enum"
    ] == ["mobile_security", "corporate_security"]

    # Verify prompt contains configured areas
    messages = call_args["messages"]
    system_msg = messages[0]["content"]
    assert "PRIMARY AREAS:" in system_msg
    assert "mobile_security: Mobile bugs" in system_msg
    assert "corporate_security: Corporate IT" in system_msg
