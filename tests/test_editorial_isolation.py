"""Tests for Phase 2: Editorial and Grounding Isolation across tenant profiles."""

from unittest.mock import MagicMock, patch

import pytest

from rss_morning import classify, digest, holdings, renderers, runner
from rss_morning.models import AreaConfig, TechnologyFootprint


def test_classifier_questions_security_profile():
    """Security profile produces cybersecurity-focused classification questions."""
    q_dict = classify.build_jev_questions(profile="security")
    rel_criteria = q_dict["is_relevant"]["criteria"]["true"]
    assert "cyber security" in rel_criteria.lower()
    assert "vulnerabilities" in rel_criteria.lower()
    assert "mobile_security" in q_dict["primary_area"]["criteria"]


def test_classifier_questions_generic_markets_profile():
    """Markets/generic profile produces generic classification questions without cyber terms."""
    custom_areas = {
        "macro": AreaConfig(
            key="macro",
            label="Macro",
            description="Global macroeconomics and monetary policy",
        ),
        "equities": AreaConfig(
            key="equities",
            label="Equities",
            description="Public equities and asset markets",
        ),
    }
    q_dict = classify.build_jev_questions(areas=custom_areas, profile="markets")
    rel_criteria = q_dict["is_relevant"]["criteria"]["true"]
    assert "cyber security" not in rel_criteria.lower()
    assert "vulnerabilities" not in rel_criteria.lower()
    assert "cve" not in rel_criteria.lower()
    assert "exploit" not in rel_criteria.lower()
    assert "macro" in q_dict["primary_area"]["criteria"]
    assert "equities" in q_dict["primary_area"]["criteria"]
    assert "mobile_security" not in q_dict["primary_area"]["criteria"]


def test_editor_schema_security_profile():
    """Security editor schema includes exploitation_status field."""
    schema = digest.build_editor_schema(["vuln", "cloud"], profile="security")
    story_props = schema["properties"]["stories"]["items"]["properties"]
    assert "exploitation_status" in story_props
    assert "confirmed_in_the_wild" in story_props["exploitation_status"]["enum"]
    assert "public_poc" in story_props["exploitation_status"]["enum"]


def test_editor_schema_generic_profile():
    """Generic/markets editor schema omits exploitation_status field entirely."""
    schema = digest.build_editor_schema(["macro", "equities"], profile="markets")
    story_props = schema["properties"]["stories"]["items"]["properties"]
    assert "exploitation_status" not in story_props


def test_generate_digest_prompt_security_profile():
    """Security profile includes Risky Business style, Mobile Security, Passkeys, and invariants."""
    articles = [
        {
            "url": "https://example.com/1",
            "title": "Zero-Day Vulnerability",
            "category": "mobile_security",
            "text": "Critical zero-day actively exploited.",
        }
    ]
    tech = TechnologyFootprint(
        description="Core tech stack",
        technologies=("Android", "Passkeys"),
    )
    captured_messages = []

    def fake_complete(messages, **kwargs):
        captured_messages.extend(messages)
        return {
            "executive_summary": {"bottom_line": "Blah", "key_points": []},
            "topics": [],
            "stories": [],
        }

    with patch("rss_morning.digest.complete_structured", fake_complete):
        digest.generate_digest(
            articles,
            profile="security",
            technologies=tech,
        )

    system_content = captured_messages[0]["content"]
    assert "APPLICATION-WIDE GROUNDING INVARIANTS" in system_content
    assert "Risky Business" in system_content
    assert "Mobile Security" in system_content
    assert "Passkeys" in system_content


def test_generate_digest_prompt_markets_profile_isolation():
    """Markets profile contains markets policy, grounding rules, and NO cyber directives."""
    articles = [
        {
            "url": "https://example.com/fed",
            "title": "Fed Cuts Rates by 25bps",
            "category": "macro_rates",
            "text": "The Federal Reserve lowered interest rates today.",
        }
    ]
    tech_watchlist = TechnologyFootprint(
        description="Portfolio macro themes",
        technologies=("Semiconductors", "Treasuries"),
    )
    tenant_rules = (
        "Strict Rule: Always distinguish between market consensus and actual policy."
    )
    captured_messages = []

    def fake_complete(messages, **kwargs):
        captured_messages.extend(messages)
        return {
            "executive_summary": {"bottom_line": "Blah", "key_points": []},
            "topics": [],
            "stories": [],
        }

    with patch("rss_morning.digest.complete_structured", fake_complete):
        digest.generate_digest(
            articles,
            system_prompt="Act as an institutional macro editor.",
            profile="markets",
            grounding_rules=tenant_rules,
            technologies=tech_watchlist,
        )

    system_content = captured_messages[0]["content"]
    assert "APPLICATION-WIDE GROUNDING INVARIANTS" in system_content
    assert "Act as an institutional macro editor." in system_content
    assert "TENANT GROUNDING RULES:\nStrict Rule:" in system_content
    assert "Semiconductors" in system_content
    assert "EDITORIAL WATCHLIST & PRIORITY DIRECTIVE" in system_content

    # Strict isolation checks: zero cyber references
    lower_content = system_content.lower()
    assert "risky business" not in lower_content
    assert "passkeys" not in lower_content
    assert "mobile security (android and ios" not in lower_content
    assert "cve" not in lower_content
    assert "sentinel" not in lower_content
    assert "malware" not in lower_content


def test_holdings_parsing_and_context_generation(tmp_path):
    """Holdings parser validates schema and formats strictly grounded context."""
    holdings_file = tmp_path / "holdings.toml"
    holdings_file.write_text(
        """
as_of = "2026-03-31"

[[holdings]]
asset = "Apple Inc."
ticker = "AAPL"
asset_class = "Equities"
currency = "USD"
weight_pct = 5.2
notes = "Core long position"

[[holdings]]
asset = "US 10Y Treasury"
ticker = "UST10Y"
asset_class = "Fixed Income"
currency = "USD"
"""
    )

    data = holdings.load_holdings(holdings_file)
    assert data.as_of == "2026-03-31"
    assert len(data.items) == 2
    assert data.items[0].ticker == "AAPL"
    assert data.items[0].weight_pct == 5.2

    context = holdings.format_holdings_context(data)
    assert "AS OF 2026-03-31" in context
    assert "AAPL" in context
    assert "UST10Y" in context
    assert "Holdings data is NOT evidence for current prices" in context
    assert "Do NOT disclose private client details" in context


def test_holdings_missing_file_fails_visibly(tmp_path):
    """Missing holdings file raises FileNotFoundError."""
    missing = tmp_path / "nonexistent.toml"
    with pytest.raises(FileNotFoundError):
        holdings.load_holdings(missing)


def test_holdings_malformed_fails_visibly(tmp_path):
    """Malformed holdings file without as_of raises ValueError."""
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("[[holdings]]\nasset = 'AAPL'\n")
    with pytest.raises(ValueError, match="missing required non-empty 'as_of'"):
        holdings.load_holdings(bad_file)


def test_holdings_not_injected_unless_configured():
    """Digest prompt does not include holdings block if holdings_context is None."""
    articles = [{"url": "https://example.com/1", "title": "A", "text": "B"}]
    captured_messages = []

    def fake_complete(messages, **kwargs):
        captured_messages.extend(messages)
        return {
            "executive_summary": {"bottom_line": "Blah", "key_points": []},
            "topics": [],
            "stories": [],
        }

    with patch("rss_morning.digest.complete_structured", fake_complete):
        digest.generate_digest(articles, profile="markets", holdings_context=None)

    system_content = captured_messages[0]["content"]
    assert "TENANT PORTFOLIO HOLDINGS CONTEXT" not in system_content


def test_rendering_security_branding_and_badges():
    """Security profile email rendering produces Sentinel branding and exploit badges."""
    payload = {
        "executive_summary": {"bottom_line": "Summary", "key_points": []},
        "topics": [
            {
                "id": "top-1",
                "title": "Threat Landscape",
                "synthesis": "Overview",
                "story_ids": ["st-1"],
            }
        ],
        "stories": [
            {
                "id": "st-1",
                "tier": "critical",
                "exploitation_status": "confirmed_in_the_wild",
                "primary_area": "vulnerability",
                "title": "Zero Day in Kernel",
                "summary": "Active zero day.",
                "source_urls": ["https://example.com"],
            }
        ],
    }

    html = renderers.build_email_html(payload, is_summary=True, profile="security")
    text = renderers.build_email_text(payload, is_summary=True, profile="security")

    assert "The Sentinel" in html
    assert "In-The-Wild Exploited" in html
    assert "THE SENTINEL DIGEST" in text
    assert "IN-THE-WILD EXPLOITED" in text


def test_rendering_markets_custom_title_and_no_sentinel_branding():
    """Markets profile email rendering applies custom title and removes Sentinel branding and exploit badges."""
    payload = {
        "executive_summary": {"bottom_line": "Market Summary", "key_points": []},
        "topics": [
            {
                "id": "top-1",
                "title": "Rates & FX",
                "synthesis": "Fed easing expectations.",
                "story_ids": ["st-1"],
            }
        ],
        "stories": [
            {
                "id": "st-1",
                "tier": "important",
                "primary_area": "macro_rates",
                "title": "Treasuries Rally",
                "summary": "10-year yields slide 5 basis points.",
                "source_urls": ["https://bloomberg.com/bond"],
            }
        ],
    }

    html = renderers.build_email_html(
        payload,
        is_summary=True,
        profile="markets",
        title="Global Markets Morning",
        subtitle="Daily Institutional Briefing",
    )
    text = renderers.build_email_text(
        payload,
        is_summary=True,
        profile="markets",
        title="Global Markets Morning",
        subtitle="Daily Institutional Briefing",
    )

    assert "Global Markets Morning" in html
    assert "Daily Institutional Briefing" in html
    assert "The Sentinel" not in html
    assert "In-The-Wild Exploited" not in html

    assert "GLOBAL MARKETS MORNING - DAILY INSTITUTIONAL BRIEFING" in text
    assert "THE SENTINEL DIGEST" not in text
    assert "IN-THE-WILD EXPLOITED" not in text


def test_dry_run_never_sends_email_summary_false(monkeypatch):
    """Dry run (--llm-dry-run) must never send email even when summary=False."""
    from datetime import datetime, timezone

    email_mock = MagicMock()
    monkeypatch.setattr(runner, "send_email_report", email_mock)
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda path: [runner.FeedConfig("Cat", "Feed", "url")],
    )
    fake_entry = runner.FeedEntry(
        link="https://example.com/1",
        category="Cat",
        title="Title 1",
        published=datetime.now(timezone.utc),
        summary="Summary 1",
    )
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda feed: [fake_entry],
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, limit, cutoff: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: runner.ArticleContent(text="Article body", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)

    config = runner.RunConfig(
        feeds_file="feeds.xml",
        limit=5,
        max_age_hours=None,
        summary=False,
        classify=False,
        email_to="recipient@example.com",
        llm_dry_run=True,
    )

    result = runner.execute(config)
    email_mock.assert_not_called()
    assert result.email_payload is None
