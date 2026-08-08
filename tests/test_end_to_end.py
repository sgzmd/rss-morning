import json
from datetime import datetime, timezone

from rss_morning import cli, runner
from rss_morning.articles import ArticleContent
from rss_morning.models import FeedEntry


def test_cli_pipeline_without_external_side_effects(monkeypatch, tmp_path, capsys):
    """Exercise config, CLI, orchestration, and JSON output with fake boundaries."""
    feeds_path = tmp_path / "feeds.opml"
    feeds_path.write_text(
        """\
<opml version="2.0">
  <body>
    <outline text="Engineering">
      <outline type="rss" text="Example" xmlUrl="https://feeds.example/rss" />
    </outline>
  </body>
</opml>
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.xml"
    config_path.write_text(
        """\
<config>
  <feeds>feeds.opml</feeds>
  <limit>1</limit>
  <summary>false</summary>
  <concurrency>1</concurrency>
</config>
""",
        encoding="utf-8",
    )

    feed_calls = []
    article_calls = []

    def fake_fetch_feed(feed):
        feed_calls.append(feed)
        return [
            FeedEntry(
                link="https://articles.example/story",
                title="Original title",
                category=feed.category,
                published=datetime(2025, 1, 2, tzinfo=timezone.utc),
                summary="Feed summary",
            )
        ]

    def fake_fetch_article(url, extractor):
        article_calls.append((url, extractor))
        return ArticleContent(
            text="Article body", image="https://articles.example/hero.jpg"
        )

    monkeypatch.setattr(runner, "fetch_feed_entries", fake_fetch_feed)
    monkeypatch.setattr(runner, "fetch_article_content", fake_fetch_article)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **_kwargs: text)
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "send_email_report",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("email must not be sent without a configured recipient")
        ),
    )

    assert cli.main(["--config", str(config_path)]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == [
        {
            "url": "https://articles.example/story",
            "category": "Engineering",
            "title": "Original title",
            "summary": "Feed summary",
            "published": "2025-01-02T00:00:00+00:00",
            "text": "Article body",
            "image": "https://articles.example/hero.jpg",
        }
    ]
    assert len(feed_calls) == 1
    assert feed_calls[0].url == "https://feeds.example/rss"
    assert article_calls == [("https://articles.example/story", "newspaper")]
