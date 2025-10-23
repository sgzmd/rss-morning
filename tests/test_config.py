import textwrap

import pytest

from rss_morning.config import parse_feeds_config
from rss_morning.models import FeedConfig


def test_parse_feeds_config_parses_nested_categories(tmp_path):
    opml = tmp_path / "feeds.xml"
    opml.write_text(
        textwrap.dedent(
            """\
            <opml version="2.0">
              <body>
                <outline text="Tech">
                  <outline text="Engineering">
                    <outline type="rss" text="Eng Blog" xmlUrl="https://example.com/eng.xml" />
                  </outline>
                  <outline type="rss" text="Tech Blog" xmlUrl="https://example.com/tech.xml" />
                </outline>
                <outline text="Standalone" type="rss" xmlUrl="https://example.com/standalone.xml" />
              </body>
            </opml>
            """
        ),
        encoding="utf-8",
    )

    feeds = parse_feeds_config(str(opml))

    assert len(feeds) == 3
    assert feeds[0] == FeedConfig(category="Engineering", title="Eng Blog", url="https://example.com/eng.xml")
    assert feeds[1] == FeedConfig(category="Tech", title="Tech Blog", url="https://example.com/tech.xml")
    assert feeds[2] == FeedConfig(category="Standalone", title="Standalone", url="https://example.com/standalone.xml")


def test_parse_feeds_config_missing_body_raises(tmp_path):
    opml = tmp_path / "feeds.xml"
    opml.write_text("<opml version='2.0'></opml>", encoding="utf-8")

    with pytest.raises(ValueError):
        parse_feeds_config(str(opml))
