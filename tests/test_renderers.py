from rss_morning import renderers


def test_build_email_html_for_summary_payload():
    payload = {
        "summaries": [
            {
                "url": "https://example.com/a",
                "summary": {"title": "Title", "what": "Thing", "so-what": "Impact", "now-what": "Next"},
            }
        ]
    }

    html = renderers.build_email_html(payload, is_summary=True)

    assert "Insight 1" in html
    assert "Impact" in html
    assert "View Article" in html


def test_build_email_text_for_articles_list():
    payload = [
        {
            "title": "Example",
            "summary": "Summary",
            "text": "Body",
            "url": "https://example.com",
        }
    ]

    text = renderers.build_email_text(payload, is_summary=False)

    assert "Example" in text
    assert "Body" in text
