"""Tests for Phase 3: Per-tenant delivery, observable failures, and cron isolation."""

import os
import subprocess
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from rss_morning import cli, emailing, runner
from rss_morning.articles import ArticleContent
from rss_morning.models import FeedEntry


@pytest.fixture
def two_tenants(tmp_path):
    """Create two fully isolated tenant configurations in tmp_path."""
    tenants_dir = tmp_path / "configs" / "tenants"
    sec_dir = tenants_dir / "tenant-sec"
    sec_dir.mkdir(parents=True)
    mkt_dir = tenants_dir / "tenant-mkt"
    mkt_dir.mkdir(parents=True)

    # Feeds for tenant-sec
    (sec_dir / "feeds.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head><title>Sec Feeds</title></head>
  <body>
    <outline text="Sec" type="rss" xmlUrl="https://sec.example.com/rss"/>
  </body>
</opml>"""
    )

    # Config for tenant-sec
    (sec_dir / "config.toml").write_text(
        """
feeds = "feeds.xml"
summary = true
profile = "security"

[email]
to = "security-team@example.com"
from = "sec-brief@example.com"
subject = "Daily Cyber Brief"
"""
    )

    # Feeds for tenant-mkt
    (mkt_dir / "feeds.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head><title>Markets Feeds</title></head>
  <body>
    <outline text="Markets" type="rss" xmlUrl="https://mkt.example.com/rss"/>
  </body>
</opml>"""
    )

    # Config for tenant-mkt
    (mkt_dir / "config.toml").write_text(
        """
feeds = "feeds.xml"
summary = true
profile = "markets"
title = "Global Markets Intelligence"
subtitle = "Daily Executive Briefing"
timezone = "America/New_York"

[email]
to = "markets-team@example.com"
from = "mkt-brief@example.com"
subject = "Daily Markets Brief"
"""
    )

    return {"base": tenants_dir, "sec": sec_dir, "mkt": mkt_dir}


def test_two_tenants_isolated_delivery(two_tenants, monkeypatch):
    """Running two tenants with mocked delivery sends strictly isolated emails to configured recipients."""
    sent_emails = []

    class FakeEmails:
        @staticmethod
        def send(email_dict):
            sent_emails.append(email_dict)
            mock_res = MagicMock()
            mock_res.id = f"msg-{len(sent_emails)}"
            return mock_res

    fake_resend = MagicMock()
    fake_resend.Emails = FakeEmails
    monkeypatch.setattr(emailing, "resend", fake_resend)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_dummy_key_12345")

    # Mock fetch and digest generation to produce deterministic tenant outputs
    sec_entry = FeedEntry(
        link="https://sec.example.com/article1",
        category="vulnerability",
        title="Zero-Day Vulnerability Found",
        published=datetime.now(timezone.utc),
        summary="Active vulnerability details.",
    )
    mkt_entry = FeedEntry(
        link="https://mkt.example.com/article2",
        category="macro",
        title="Central Bank Rate Decision",
        published=datetime.now(timezone.utc),
        summary="Interest rate decision announcement.",
    )

    def fake_fetch_entries(feed):
        if "sec.example.com" in feed.url:
            return [sec_entry]
        return [mkt_entry]

    monkeypatch.setattr(runner, "fetch_feed_entries", fake_fetch_entries)
    from rss_morning.articles import ArticleContent

    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="Body text for " + url, image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)
    monkeypatch.setattr(
        runner,
        "classify_entry",
        lambda *args, **kwargs: runner.ClassificationDecision(
            relevance_probability=0.95,
            primary_area="vulnerability",
            area_confidence=0.9,
            is_plausible=True,
        ),
    )

    # Patch tenant dir resolution to use test fixture base
    monkeypatch.setattr("rss_morning.tenant.DEFAULT_TENANTS_DIR", two_tenants["base"])

    # 1. Execute Security Tenant via CLI
    ret_sec = cli.main(["--tenant", "tenant-sec", "--llm-dry-run"])
    assert ret_sec == 0
    # Dry run must not send
    assert len(sent_emails) == 0

    # Execute Security Tenant without dry run
    def fake_generate_digest(articles, **kwargs):
        profile = kwargs.get("profile", "security")
        if profile == "security":
            return {
                "executive_summary": {
                    "bottom_line": "Security threat summary.",
                    "key_points": [],
                },
                "topics": [
                    {
                        "id": "top-sec",
                        "title": "Critical Exploits",
                        "synthesis": "Vulnerability overview.",
                        "story_ids": ["s1"],
                    }
                ],
                "stories": [
                    {
                        "id": "s1",
                        "tier": "critical",
                        "exploitation_status": "confirmed_in_the_wild",
                        "primary_area": "vulnerability",
                        "title": "Kernel 0-Day",
                        "summary": "Exploitation confirmed.",
                        "source_urls": ["https://sec.example.com/article1"],
                    }
                ],
            }
        else:
            return {
                "executive_summary": {
                    "bottom_line": "Market rally summary.",
                    "key_points": [],
                },
                "topics": [
                    {
                        "id": "top-mkt",
                        "title": "Rates & Debt",
                        "synthesis": "Rates overview.",
                        "story_ids": ["m1"],
                    }
                ],
                "stories": [
                    {
                        "id": "m1",
                        "tier": "important",
                        "primary_area": "macro",
                        "title": "Bond Yields Drop",
                        "summary": "10-year yields fall.",
                        "source_urls": ["https://mkt.example.com/article2"],
                    }
                ],
            }

    monkeypatch.setattr(runner, "generate_digest", fake_generate_digest)

    ret_sec_live = cli.main(["--tenant", "tenant-sec"])
    assert ret_sec_live == 0
    assert len(sent_emails) == 1
    sec_mail = sent_emails[0]

    assert sec_mail["to"] == ["security-team@example.com"]
    assert sec_mail["from"] == "sec-brief@example.com"
    assert sec_mail["subject"] == "Daily Cyber Brief"
    assert "In-The-Wild Exploited" in sec_mail["html"]
    assert "The Sentinel" in sec_mail["html"]
    assert "Global Markets" not in sec_mail["html"]

    # 2. Execute Markets Tenant via CLI
    ret_mkt_live = cli.main(["--tenant", "tenant-mkt"])
    assert ret_mkt_live == 0
    assert len(sent_emails) == 2
    mkt_mail = sent_emails[1]

    assert mkt_mail["to"] == ["markets-team@example.com"]
    assert mkt_mail["from"] == "mkt-brief@example.com"
    assert mkt_mail["subject"] == "Daily Markets Brief"
    assert "Global Markets Intelligence" in mkt_mail["html"]
    assert "Daily Executive Briefing" in mkt_mail["html"]
    assert "The Sentinel" not in mkt_mail["html"]
    assert "In-The-Wild Exploited" not in mkt_mail["html"]
    assert "Kernel 0-Day" not in mkt_mail["html"]


def test_delivery_failure_is_observable(monkeypatch, two_tenants):
    """When email sending fails (missing key, sender, or API error), cli returns exit code 1."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr("rss_morning.tenant.DEFAULT_TENANTS_DIR", two_tenants["base"])
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda feed: [
            FeedEntry(
                link="https://sec.example.com/1",
                category="sec",
                title="T",
                published=datetime.now(timezone.utc),
            )
        ],
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="body", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)
    monkeypatch.setattr(
        runner,
        "generate_digest",
        lambda articles, **kwargs: {
            "executive_summary": {"bottom_line": "B", "key_points": []},
            "topics": [],
            "stories": [],
        },
    )

    exit_code = cli.main(["--tenant", "tenant-sec"])
    # Missing RESEND_API_KEY causes observable delivery failure exit code 1
    assert exit_code == 1


def test_dry_run_never_sends_email_summary_true(two_tenants, monkeypatch):
    """Dry run with summary=True never sends email and returns exit code 0."""
    send_mock = MagicMock()
    monkeypatch.setattr(emailing, "send_email_report", send_mock)
    monkeypatch.setattr("rss_morning.tenant.DEFAULT_TENANTS_DIR", two_tenants["base"])
    monkeypatch.setattr(
        runner,
        "fetch_feed_entries",
        lambda feed: [
            FeedEntry(
                link="https://mkt.example.com/1",
                category="macro",
                title="T",
                published=datetime.now(timezone.utc),
            )
        ],
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **kwargs: ArticleContent(text="body", image=None),
    )
    monkeypatch.setattr(runner, "truncate_text", lambda text, **kwargs: text)
    monkeypatch.setattr(
        runner,
        "classify_entry",
        lambda *args, **kwargs: runner.ClassificationDecision(
            relevance_probability=0.95,
            primary_area="macro",
            area_confidence=0.9,
            is_plausible=True,
        ),
    )

    exit_code = cli.main(["--tenant", "tenant-mkt", "--llm-dry-run"])
    assert exit_code == 0
    send_mock.assert_not_called()


def test_cron_wrapper_script_usage_validation():
    """run-tenant-cron.sh requires a tenant-id argument and exits with code 2 if omitted."""
    script_path = os.path.abspath("scripts/run-tenant-cron.sh")
    result = subprocess.run(
        [script_path],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Missing tenant-id" in result.stderr


def test_tenant_timezone_aware_email_subject_and_date():
    """Tenant configured timezone formats dates in default subject."""
    from zoneinfo import ZoneInfo

    ny_tz = ZoneInfo("America/New_York")
    ny_now = datetime.now(ny_tz)
    expected_date = ny_now.strftime("%d %B %Y")
    subj = runner._build_default_email_subject(
        tz_name="America/New_York", title="Markets Brief"
    )
    assert subj == f"Markets Brief - {expected_date}"
