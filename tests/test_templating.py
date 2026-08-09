from rss_morning import templating
from rss_morning.templating import get_environment


def test_get_environment_registers_nl2br_filter():
    env = get_environment()
    assert "nl2br" in env.filters
    rendered = env.from_string("{{ value | nl2br }}").render(value="line1\nline2")
    assert "line1<br>line2" in rendered


def test_template_filters_handle_empty_domains_and_markdown():
    assert templating._nl2br(None) == ""
    assert templating._extract_domain(None) == ""
    assert templating._extract_domain("https://www.example.com/path") == "example.com"
    assert templating._extract_domain("https://example.com/path") == "example.com"
    assert templating._extract_domain("http://[") == "http://["
    assert templating._render_markdown(None) == ""

    rendered = str(
        templating._render_markdown(
            "- **safe**\n- <script>unsafe</script>\n\nparagraph"
        )
    )
    assert '<ul style="padding-left: 20px;' in rendered
    assert '<li style="margin-bottom: 4px;">' in rendered
    assert '<p style="margin: 0 0 8px 0;">' in rendered
    assert "<script>" not in rendered


def test_get_environment_returns_cached_instance():
    assert get_environment() is get_environment()
