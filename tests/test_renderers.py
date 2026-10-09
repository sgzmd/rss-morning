from rss_morning import renderers


def test_build_email_html_for_edition_payload():
    payload = {
        "overview": "Calm morning with one critical advisory.",
        "attention": [
            {
                "title": "Critical Perimeter Auth Bypass",
                "summary": "Vendors disclosed critical zero-day flaw.",
                "source_urls": ["https://example.com/vpn-advisory"],
                "primary_area": "corporate_it",
                "urgency_rationale": "Active exploitation in the wild.",
            }
        ],
        "watch": [
            {
                "title": "Emerging Phishing Trend",
                "summary": "Novel technique using SVG files.",
                "source_urls": ["https://example.com/phish"],
                "primary_area": "fraud_abuse",
            }
        ],
    }

    html = renderers.build_email_html(payload, is_summary=True)

    assert "Calm morning with one critical advisory." in html
    assert "Requires Attention" in html
    assert "Critical Perimeter Auth Bypass" in html
    assert "Active exploitation in the wild." in html
    assert "corporate_it" in html
    assert "Watchlist & Emerging Items" in html
    assert "Emerging Phishing Trend" in html
    assert "fraud_abuse" in html
    assert "example.com" in html


def test_build_email_text_for_edition_payload():
    payload = {
        "overview": "Calm morning with one critical advisory.",
        "attention": [
            {
                "title": "Critical Perimeter Auth Bypass",
                "summary": "Vendors disclosed critical zero-day flaw.",
                "source_urls": ["https://example.com/vpn-advisory"],
                "primary_area": "corporate_it",
                "urgency_rationale": "Active exploitation in the wild.",
            }
        ],
        "watch": [
            {
                "title": "Emerging Phishing Trend",
                "summary": "Novel technique using SVG files.",
                "source_urls": ["https://example.com/phish"],
                "primary_area": "fraud_abuse",
            }
        ],
    }

    text = renderers.build_email_text(payload, is_summary=True)

    assert "Overview:" in text
    assert "Calm morning with one critical advisory." in text
    assert "ATTENTION:" in text
    assert "Critical Perimeter Auth Bypass [corporate_it]" in text
    assert "Why it matters: Active exploitation in the wild." in text
    assert "WATCH:" in text
    assert "Emerging Phishing Trend [fraud_abuse]" in text


def test_build_email_html_and_text_pyramid_structure():
    payload = {
        "executive_summary": {
            "bottom_line": "Perimeter devices under coordinated exploitation.",
            "key_points": [
                {
                    "text": "Fortinet zero-day added to CISA KEV.",
                    "story_ids": ["s1"],
                }
            ],
        },
        "topics": [
            {
                "id": "t1",
                "title": "Perimeter Compromise Surge",
                "synthesis": "Threat actors are weaponizing edge devices.",
                "story_ids": ["s1"],
            }
        ],
        "stories": [
            {
                "id": "s1",
                "title": "Fortinet FortiMail Zero-Day",
                "primary_area": "corporate_security",
                "tier": "critical",
                "exploitation_status": "confirmed_in_the_wild",
                "summary": "Path traversal flaw exploited unauthenticated.",
                "key_facts": ["CVE-2026-104286", "CVSS 9.8"],
                "why_it_matters": "Confirmed active exploitation on edge mail.",
                "article_ids": ["art-1"],
                "source_urls": ["https://theregister.com/fortinet"],
            }
        ],
    }

    html = renderers.build_email_html(payload, is_summary=True)
    assert "Level 1 &bull; Executive Summary" in html
    assert "Perimeter devices under coordinated exploitation." in html
    assert "Priority Takeaways" in html
    assert "Fortinet zero-day added to CISA KEV." in html
    assert "Level 2 &bull; Key Topics &amp; Developments" in html
    assert "Perimeter Compromise Surge" in html
    assert "Fortinet FortiMail Zero-Day" in html
    assert "CVE-2026-104286" in html
    assert "In-The-Wild Exploited" in html
    assert "Technical Deep Dives" not in html

    text = renderers.build_email_text(payload, is_summary=True)
    assert "LEVEL 1: EXECUTIVE SUMMARY" in text
    assert "THE BOTTOM LINE:" in text
    assert "LEVEL 2: KEY TOPICS & DEVELOPMENTS" in text
    assert "=== TOPIC: PERIMETER COMPROMISE SURGE ===" in text
    assert "[CRITICAL | IN-THE-WILD EXPLOITED]" in text
    assert "TECHNICAL DEEP DIVES" not in text


def test_build_email_html_for_raw_articles():
    payload = [
        {
            "title": "Raw Story",
            "summary": "Raw summary",
            "text": "Raw article text",
            "url": "https://example.com/raw",
            "image": "https://example.com/hero.jpg",
        }
    ]

    html = renderers.build_email_html(payload, is_summary=False)

    assert "Raw Story" in html
    assert "Raw summary" in html
    assert 'src="https://example.com/hero.jpg"' in html
    assert "Read more on example.com" in html


def test_build_email_text_for_raw_articles():
    payload = [
        {
            "title": "Raw Story",
            "summary": "Raw summary",
            "text": "Raw article text",
            "url": "https://example.com/raw",
            "image": "https://example.com/hero.jpg",
        }
    ]

    text = renderers.build_email_text(payload, is_summary=False)

    assert "Title: Raw Story" in text
    assert "Summary: Raw summary" in text
    assert "Image: https://example.com/hero.jpg" in text
    assert "Link: https://example.com/raw" in text


def test_build_email_html_handles_fallback():
    html = renderers.build_email_html(
        payload="raw text", is_summary=False, fallback="raw text"
    )
    assert "raw text" in html
    assert "<pre" in html


def test_build_email_text_handles_fallback():
    text = renderers.build_email_text(
        payload="raw text", is_summary=False, fallback="raw text"
    )
    assert text.strip() == "raw text"


def test_prepare_sections_by_area():
    from rss_morning.models import AreaConfig
    from rss_morning.renderers import prepare_sections_by_area

    areas = {
        "mobile_security": AreaConfig(
            key="mobile_security",
            label="Mobile Security",
            description="Mobile",
            threshold=0.40,
        ),
        "corporate_security": AreaConfig(
            key="corporate_security",
            label="Corporate Security",
            description="Corp",
            threshold=0.45,
        ),
        "empty_area": AreaConfig(
            key="empty_area",
            label="Empty Area",
            description="Empty",
            threshold=0.50,
        ),
    }

    payload = {
        "attention": [
            {
                "title": "Corporate Zero-Day",
                "summary": "Exploit in VPN",
                "primary_area": "corporate_security",
                "source_urls": ["https://corp.com"],
            }
        ],
        "watch": [
            {
                "title": "iOS Malware",
                "summary": "New Trojan",
                "primary_area": "mobile_security",
                "source_urls": ["https://mobile.com"],
            }
        ],
    }

    sections = prepare_sections_by_area(payload, areas)
    # Order matches configured areas (mobile_security first, then corporate_security)
    assert len(sections) == 2
    assert sections[0]["key"] == "mobile_security"
    assert sections[0]["label"] == "Mobile Security"
    assert len(sections[0]["items"]) == 1
    assert sections[0]["items"][0]["is_attention"] is False

    assert sections[1]["key"] == "corporate_security"
    assert sections[1]["label"] == "Corporate Security"
    assert len(sections[1]["items"]) == 1
    assert sections[1]["items"][0]["is_attention"] is True


def test_build_email_html_and_text_with_configured_areas():
    from rss_morning.models import AreaConfig

    areas = {
        "mobile_security": AreaConfig(
            key="mobile_security",
            label="Mobile Security",
            description="Mobile",
            threshold=0.40,
        )
    }

    payload = {
        "overview": "Overview of today",
        "attention": [
            {
                "title": "Severe iOS Spyware",
                "summary": "In-the-wild zero-click exploit",
                "source_urls": ["https://example.com/ios"],
                "primary_area": "mobile_security",
                "urgency_rationale": "Actively used in attacks",
            }
        ],
        "watch": [],
    }

    html = renderers.build_email_html(payload, is_summary=True, areas=areas)
    assert "Mobile Security" in html
    assert "Severe iOS Spyware" in html
    assert "Requires Attention" in html
    assert "Actively used in attacks" in html

    text = renderers.build_email_text(payload, is_summary=True, areas=areas)
    assert "=== MOBILE SECURITY ===" in text
    assert "[ATTENTION] Severe iOS Spyware" in text
    assert "Why it matters: Actively used in attacks" in text


def test_build_email_html_responsive_mobile_optimizations():
    payload = {
        "executive_summary": {
            "bottom_line": "Major zero-day exploitation across perimeter devices.",
            "key_points": [
                {
                    "text": "Citrix NetScaler exploited in the wild.",
                    "story_ids": ["s1"],
                }
            ],
        },
        "topics": [
            {
                "id": "t1",
                "title": "Perimeter Compromise",
                "synthesis": "Edge devices under active attack.",
                "story_ids": ["s1"],
            }
        ],
        "stories": [
            {
                "id": "s1",
                "title": "Citrix Zero-Day",
                "primary_area": "corporate_security",
                "tier": "critical",
                "exploitation_status": "confirmed_in_the_wild",
                "summary": "Unauthenticated buffer overflow in VPN gateway.",
                "key_facts": ["CVE-2026-107406", "CVSS 9.5"],
                "why_it_matters": "Active exploitation observed.",
                "source_urls": ["https://example.com/citrix"],
            }
        ],
    }

    html = renderers.build_email_html(payload, is_summary=True)

    # 1. Meta tags for Apple Mail and dark mode support
    assert 'name="x-apple-disable-message-reformatting"' in html
    assert 'name="color-scheme" content="light dark"' in html

    # 2. Outer table responsive class
    assert 'class="outer-cell"' in html

    # 3. Level 1 executive summary responsive class
    assert 'class="exec-summary"' in html

    # 4. Level 2 topic unboxing (open section without nested border/padding)
    assert 'class="topic-section"' in html
    assert "padding: 20px 22px" not in html
    assert "border: 1px solid #e8eaed; border-radius: 6px;" not in html

    # 5. Story card and Key Facts responsive classes
    assert 'class="story-card"' in html
    assert 'class="key-facts-box"' in html

    # 6. CSS media queries for mobile viewports (<= 600px)
    assert "@media only screen and (max-width: 600px)" in html
    assert ".outer-cell" in html
    assert ".content-wrapper" in html
    assert ".story-card" in html
    assert ".key-facts-box" in html
    assert ".topic-section" in html
