"""Tests for Phase 4: Editorial digest generation via OpenRouter (Pyramid Principle)."""

import json
from unittest.mock import patch

import pytest

from rss_morning.config import DigestConfig
from rss_morning.digest import (
    DEFAULT_EDITORIAL_POLICY,
    EDITION_SCHEMA,
    build_editor_schema,
    build_editorial_input,
    generate_digest,
    resolve_edition_ids,
)
from rss_morning.models import AreaConfig


@pytest.fixture
def mock_complete_structured():
    with patch("rss_morning.digest.complete_structured") as mock_complete:
        yield mock_complete


def test_generate_digest_pyramid_flow(mock_complete_structured):
    """Verify generate_digest executes single editor call for stories, topics, and executive summary."""
    # Editor output
    editor_payload = {
        "stories": [
            {
                "id": "s1",
                "title": "Critical Perimeter Auth Bypass",
                "primary_area": "corporate_security",
                "tier": "critical",
                "exploitation_status": "confirmed_in_the_wild",
                "summary": "Vendor disclosed auth bypass actively exploited in the wild.",
                "key_facts": ["CVE-2026-9999", "CVSS 9.8"],
                "why_it_matters": "Active exploitation on perimeter gateways.",
                "article_ids": ["art-1", "art-2"],
            },
            {
                "id": "s2",
                "title": "Routine Chrome Security Update",
                "primary_area": "end_user_security",
                "tier": "notable",
                "exploitation_status": "not_reported",
                "summary": "Google resolved browser memory safety issues.",
                "key_facts": ["12 CVEs fixed"],
                "why_it_matters": "Routine desktop browser maintenance.",
                "article_ids": ["art-3"],
            },
        ],
        "topics": [
            {
                "id": "t1",
                "title": "Enterprise Gateway Compromises",
                "synthesis": "Attackers continue targeting internet-exposed corporate gateways.",
                "story_ids": ["s1"],
            }
        ],
        "executive_summary": {
            "bottom_line": "Perimeter gateways face confirmed active exploitation today.",
            "key_points": [
                {
                    "text": "Critical perimeter authentication bypass is being actively weaponized.",
                    "story_ids": ["s1"],
                }
            ],
        },
    }

    mock_complete_structured.return_value = editor_payload

    articles = [
        {
            "id": "art-1",
            "url": "https://example.com/vpn-1",
            "title": "Advisory 1",
            "text": "Full body text 1",
            "summary": "Summary 1",
            "primary_area": "corporate_security",
        },
        {
            "id": "art-2",
            "url": "https://example.com/vpn-2",
            "title": "Advisory 2",
            "text": "Full body text 2",
            "summary": "Summary 2",
            "primary_area": "corporate_security",
        },
        {
            "id": "art-3",
            "url": "https://example.com/chrome-1",
            "title": "Chrome 1",
            "text": "Full body text 3",
            "summary": "Summary 3",
            "primary_area": "end_user_security",
        },
    ]

    result = generate_digest(
        articles,
        model="test/gemini-flash",
        digest_config=DigestConfig(),
    )

    # Exactly 1 editor LLM call
    assert mock_complete_structured.call_count == 1

    # Verify pyramid return fields
    exec_sum = result["executive_summary"]
    assert "Perimeter gateways face confirmed" in exec_sum["bottom_line"]
    assert len(exec_sum["key_points"]) == 1
    assert exec_sum["key_points"][0]["story_ids"] == ["s1"]

    assert len(result["topics"]) == 1
    assert result["topics"][0]["id"] == "t1"

    assert len(result["stories"]) == 2
    # Verify deduplication and URL resolution on story s1
    s1 = result["stories"][0]
    assert s1["id"] == "s1"
    assert s1["source_urls"] == [
        "https://example.com/vpn-1",
        "https://example.com/vpn-2",
    ]

    # Verify backward compatibility aliases
    assert "Perimeter gateways face confirmed" in result["overview"]
    assert len(result["attention"]) == 1
    assert result["attention"][0]["title"] == "Critical Perimeter Auth Bypass"
    assert len(result["watch"]) == 1
    assert result["watch"][0]["title"] == "Routine Chrome Security Update"


def test_generate_digest_empty_input(mock_complete_structured):
    """Verify that empty article list short-circuits without calling OpenRouter."""
    result = generate_digest([])
    assert mock_complete_structured.call_count == 0
    assert (
        result["executive_summary"]["bottom_line"]
        == "No articles retrieved for this edition."
    )
    assert result["topics"] == []
    assert result["stories"] == []
    assert result["overview"] == "No articles retrieved for this edition."
    assert result["attention"] == []
    assert result["watch"] == []


def test_generate_digest_dry_run(mock_complete_structured):
    """Verify that dry_run logs and returns placeholder without API calls."""
    articles = [{"url": "https://example.com/1", "title": "Article 1"}]
    result = generate_digest(articles, dry_run=True)
    assert mock_complete_structured.call_count == 0
    assert "Dry run overview" in result["executive_summary"]["bottom_line"]
    assert len(result["stories"]) == 1
    assert len(result["topics"]) == 1


def test_editorial_schema_has_strict_structure():
    """Verify that EDITION_SCHEMA enforces required keys and forbids additional properties."""
    assert EDITION_SCHEMA["type"] == "object"
    assert EDITION_SCHEMA["additionalProperties"] is False
    assert set(EDITION_SCHEMA["required"]) == {
        "stories",
        "topics",
        "executive_summary",
    }

    stories_schema = EDITION_SCHEMA["properties"]["stories"]["items"]
    assert stories_schema["additionalProperties"] is False
    assert set(stories_schema["required"]) == {
        "id",
        "title",
        "primary_area",
        "tier",
        "exploitation_status",
        "summary",
        "key_facts",
        "why_it_matters",
        "article_ids",
    }

    topics_schema = EDITION_SCHEMA["properties"]["topics"]["items"]
    assert topics_schema["additionalProperties"] is False
    assert set(topics_schema["required"]) == {
        "id",
        "title",
        "synthesis",
        "story_ids",
    }

    exec_schema = EDITION_SCHEMA["properties"]["executive_summary"]
    assert exec_schema["additionalProperties"] is False
    assert set(exec_schema["required"]) == {"bottom_line", "key_points"}


def test_grounding_and_anti_hallucination_guardrails_in_policy():
    """Verify that editorial policy contains strict grounding and bans internal-environment claims."""
    policy = DEFAULT_EDITORIAL_POLICY.lower()
    assert "grounding" in policy
    assert "ban internal-environment claims" in policy
    assert "never state, assume, or imply that our organization" in policy
    assert "preserve uncertainty" in policy
    assert "collapse duplicates" in policy
    assert "editorial triage" in policy


def test_resolve_edition_ids_handles_unknown_ids_safely():
    """Verify resolve_edition_ids filters out nonexistent article or story IDs gracefully."""
    articles = [
        {"id": "art-1", "url": "https://example.com/1", "title": "Story 1"},
        {"id": "art-2", "url": "https://example.com/2", "title": "Story 2"},
    ]

    editor_data = {
        "stories": [
            {
                "id": "s1",
                "title": "Known Story",
                "article_ids": ["art-1", "art-nonexistent", "art-2"],
            },
            {
                "id": "s2",
                "title": "Story with no valid articles",
                "article_ids": ["art-bogus"],
            },
        ],
        "topics": [
            {
                "id": "t1",
                "title": "Topic 1",
                "story_ids": ["s1", "s-ghost"],
            }
        ],
        "executive_summary": {
            "bottom_line": "Summary",
            "key_points": [
                {"text": "Point 1", "story_ids": ["s1", "s-ghost"]},
            ],
        },
    }

    resolved = resolve_edition_ids(editor_data, articles)

    # s1 keeps valid article URLs
    s1 = resolved["stories"][0]
    assert s1["source_urls"] == ["https://example.com/1", "https://example.com/2"]
    assert s1["article_ids"] == ["art-1", "art-2"]

    # s2 has empty source_urls
    s2 = resolved["stories"][1]
    assert s2["source_urls"] == []

    # Topic t1 drops phantom story
    assert resolved["topics"][0]["story_ids"] == ["s1"]

    # Executive summary drops phantom story
    assert resolved["executive_summary"]["key_points"][0]["story_ids"] == ["s1"]


def test_build_editorial_input_formats_articles():
    """Verify build_editorial_input serializes candidates with metadata and classifications."""
    articles = [
        {
            "id": "art-1",
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


def test_build_editor_schema_dynamic_enum():
    """Verify build_editor_schema injects primary_area enum restriction into stories."""
    schema = build_editor_schema(["mobile_security", "ai_security"])
    story_area = schema["properties"]["stories"]["items"]["properties"]["primary_area"]

    assert story_area["enum"] == ["mobile_security", "ai_security"]


def test_generate_digest_with_areas_updates_prompt_and_schema(
    mock_complete_structured,
):
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
        "stories": [],
        "topics": [],
        "executive_summary": {
            "bottom_line": "Overview text",
            "key_points": [],
        },
    }

    articles = [{"url": "https://example.com/1", "title": "Art 1"}]
    generate_digest(articles, areas=areas)

    assert mock_complete_structured.call_count == 1
    call_args = mock_complete_structured.call_args[1]

    # Verify dynamic schema used
    schema = call_args["json_schema"]
    assert schema["properties"]["stories"]["items"]["properties"]["primary_area"][
        "enum"
    ] == ["mobile_security", "corporate_security"]

    # Verify prompt contains configured areas
    messages = call_args["messages"]
    system_msg = messages[0]["content"]
    assert "CONFIGURED FOCUS AREAS & TARGET TECHNOLOGIES:" in system_msg
    assert "mobile_security (Mobile Security): Mobile bugs" in system_msg
    assert "corporate_security (Corporate Security): Corporate IT" in system_msg


def test_generate_digest_with_technologies_and_priority_focus(
    mock_complete_structured,
):
    """Verify generate_digest injects technology footprint and priority directives into prompt."""
    from rss_morning.models import TechnologyFootprint

    tech = TechnologyFootprint(
        description="Our consumer app relies on Passkeys and Android/iOS client integrity.",
        technologies=("Android Keystore", "Passkeys (WebAuthn)", "iOS Secure Enclave"),
    )

    areas = {
        "mobile_security": AreaConfig(
            key="mobile_security",
            label="Mobile Security",
            description="Mobile bugs",
            threshold=0.40,
            priority="high",
            technologies=("Android Keystore", "iOS App Attest"),
        ),
    }

    mock_complete_structured.return_value = {
        "stories": [],
        "topics": [],
        "executive_summary": {
            "bottom_line": "Overview text",
            "key_points": [],
        },
    }

    articles = [{"url": "https://example.com/1", "title": "Art 1"}]
    generate_digest(articles, areas=areas, technologies=tech)

    assert mock_complete_structured.call_count == 1
    call_args = mock_complete_structured.call_args[1]
    system_msg = call_args["messages"][0]["content"]

    assert "TECHNOLOGY FOOTPRINT & EDITORIAL PRIORITY DIRECTIVE:" in system_msg
    assert "Our consumer app relies on Passkeys" in system_msg
    assert "Passkeys (WebAuthn)" in system_msg
    assert "*PRIORITY FOCUS*: High priority topic." in system_msg
    assert "*Target Technologies*: Android Keystore, iOS App Attest" in system_msg
    assert "Mobile Security (Android and iOS platforms" in system_msg
