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
