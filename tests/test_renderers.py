from rss_morning import renderers


def test_build_email_html_for_summary_payload():
    payload = {
        "summaries": [
            {
                "url": "https://example.com/a",
                "image": "https://example.com/image.jpg",
                "category": "API and Data Security",
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

    assert "API and Data Security" in html
    assert "Impact" in html
    assert "Read Source Intelligence" in html
    assert 'src="https://example.com/image.jpg"' in html


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

    assert 'src="https://example.com/hero.jpg"' in html
    assert "Visual Story" in html


def test_build_email_html_handles_fallback():
    html = renderers.build_email_html(
        payload="raw text", is_summary=False, fallback="raw text"
    )
    assert "raw text" in html
    assert "<pre" in html


def test_build_email_html_includes_exec_summary():
    payload = {
        "exec_summary": "Top level summary.",
        "summaries": [
            {
                "url": "https://example.com/a",
                "summary": {
                    "title": "Title",
                    "what": "Thing",
                },
            }
        ],
    }

    html = renderers.build_email_html(payload, is_summary=True)
    assert "Top level summary." in html
    assert "Executive Summary" in html


def test_build_email_text_includes_exec_summary():
    payload = {
        "exec_summary": "Top level summary.",
        "summaries": [],
    }

    text = renderers.build_email_text(payload, is_summary=True)
    assert "Executive Summary:" in text
    assert "Top level summary." in text
