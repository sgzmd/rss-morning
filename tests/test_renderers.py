from rss_morning import renderers


def test_build_email_html_for_summary_payload():
    payload = {
        "summaries": [
            {
                "url": "https://example.com/a",
                "image": "https://example.com/image.jpg",
                "summary": {
                    "title": "Title",
                    "what": "Thing",
                    "so-what": "Impact",
                    "now-what": "Next",
                },
            }
        ]
    }

    html = renderers.build_email_html(payload, is_summary=True)

    assert "Insight 1" in html
    assert "Impact" in html
    assert "View Article" in html
    assert '<img src="https://example.com/image.jpg"' in html


def test_build_email_text_for_articles_list():
    payload = [
        {
            "title": "Example",
            "summary": "Summary",
            "text": "Body",
            "url": "https://example.com",
            "image": "https://example.com/hero.jpg",
        }
    ]

    text = renderers.build_email_text(payload, is_summary=False)

    assert "Example" in text
    assert "Body" in text
    assert "https://example.com/hero.jpg" in text


def test_build_email_text_for_summary_includes_image():
    payload = {
        "summaries": [
            {
                "url": "https://example.com/a",
                "image": "https://example.com/image.jpg",
                "summary": {
                    "title": "Title",
                    "what": "Thing",
                },
            }
        ]
    }

    text = renderers.build_email_text(payload, is_summary=True)

    assert "Image: https://example.com/image.jpg" in text


def test_build_email_html_includes_article_image():
    payload = [
        {
            "title": "Visual Story",
            "summary": "Summary",
            "text": "Excerpt",
            "url": "https://example.com/story",
            "image": "https://example.com/hero.jpg",
        }
    ]

    html = renderers.build_email_html(payload, is_summary=False)

    assert '<img src="https://example.com/hero.jpg"' in html
    assert "Visual Story" in html


def test_build_email_html_handles_fallback():
    html = renderers.build_email_html(
        payload="raw text", is_summary=False, fallback="raw text"
    )
    assert "<pre>raw text</pre>" in html
