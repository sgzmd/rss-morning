from rss_morning.templating import get_environment


def test_get_environment_registers_nl2br_filter():
    env = get_environment()
    assert "nl2br" in env.filters
    rendered = env.from_string("{{ value | nl2br }}").render(value="line1\nline2")
    assert "line1<br>line2" in rendered
