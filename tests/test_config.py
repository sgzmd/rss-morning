import textwrap

import pytest

from rss_morning.config import parse_app_config, parse_env_config, parse_feeds_config
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
    assert feeds[0] == FeedConfig(
        category="Engineering", title="Eng Blog", url="https://example.com/eng.xml"
    )
    assert feeds[1] == FeedConfig(
        category="Tech", title="Tech Blog", url="https://example.com/tech.xml"
    )
    assert feeds[2] == FeedConfig(
        category="Standalone",
        title="Standalone",
        url="https://example.com/standalone.xml",
    )


def test_parse_feeds_config_missing_body_raises(tmp_path):
    opml = tmp_path / "feeds.xml"
    opml.write_text("<opml version='2.0'></opml>", encoding="utf-8")

    with pytest.raises(ValueError):
        parse_feeds_config(str(opml))


def test_parse_app_config_database(tmp_path):
    from rss_morning.config import parse_app_config

    config_file = tmp_path / "config.xml"
    config_file.write_text(
        """
        <config>
            <feeds>feeds.xml</feeds>
            <limit>5</limit>
            <database>
                <enabled>true</enabled>
                <connection-string>sqlite:///test.db</connection-string>
            </database>
        </config>
        """
    )

    # Create dummy feeds.xml to satisfy parser
    (tmp_path / "feeds.xml").write_text("<opml><body></body></opml>")

    config = parse_app_config(str(config_file))
    assert config.database.enabled is True
    assert config.database.connection_string == "sqlite:///test.db"


def test_parse_app_config_prompt_loader(tmp_path):
    from rss_morning.config import parse_app_config

    config_file = tmp_path / "config.xml"
    prompt_file = tmp_path / "prompt.txt"
    prompt_content = "Please summarize this."
    prompt_file.write_text(prompt_content, encoding="utf-8")

    config_file.write_text(
        f"""
        <config>
            <feeds>feeds.xml</feeds>
            <prompt file="{prompt_file.name}" />
        </config>
        """
    )

    (tmp_path / "feeds.xml").write_text("<opml><body></body></opml>")

    config = parse_app_config(str(config_file))
    assert config.prompt == prompt_content


def test_parse_app_config_prompt_loader_missing_file_raises(tmp_path):
    from rss_morning.config import parse_app_config

    config_file = tmp_path / "config.xml"
    config_file.write_text(
        """
        <config>
            <feeds>feeds.xml</feeds>
            <prompt file="missing.txt" />
        </config>
        """
    )
    (tmp_path / "feeds.xml").write_text("<opml><body></body></opml>")

    with pytest.raises(ValueError, match="Prompt file not found"):
        parse_app_config(str(config_file))


def test_parse_app_config_prompt_missing_file_attr_raises(tmp_path):
    from rss_morning.config import parse_app_config

    config_file = tmp_path / "config.xml"
    config_file.write_text(
        """
        <config>
            <feeds>feeds.xml</feeds>
            <prompt>Some inline text</prompt>
        </config>
        """
    )
    (tmp_path / "feeds.xml").write_text("<opml><body></body></opml>")

    with pytest.raises(ValueError, match="Prompt element must have a 'file' attribute"):
        parse_app_config(str(config_file))


def test_parse_feeds_config_fallbacks_and_ignored_outline(tmp_path):
    opml = tmp_path / "feeds.xml"
    opml.write_text(
        """<opml><body>
        <outline><outline type="rss" xmlUrl="https://example.com/a" /></outline>
        <outline type="html" text="Ignored" xmlUrl="https://example.com/no" />
        </body></opml>""",
        encoding="utf-8",
    )

    assert parse_feeds_config(str(opml)) == [
        FeedConfig(
            category="Uncategorized",
            title="https://example.com/a",
            url="https://example.com/a",
        )
    ]


def test_parse_env_config_empty_and_malformed(tmp_path):
    assert parse_env_config("") == {}
    malformed = tmp_path / "env.xml"
    malformed.write_text("<environment>", encoding="utf-8")
    with pytest.raises(Exception):
        parse_env_config(str(malformed))


def test_parse_env_config_ignores_incomplete_variables(tmp_path):
    env_file = tmp_path / "env.xml"
    env_file.write_text(
        """<environment>
        <variable name="EMPTY"></variable>
        <variable>value</variable>
        </environment>""",
        encoding="utf-8",
    )
    assert parse_env_config(str(env_file)) == {}


def test_parse_app_config_rejects_missing_file_and_feeds(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_app_config(str(tmp_path / "missing.xml"))

    config_file = tmp_path / "config.xml"
    config_file.write_text("<config />", encoding="utf-8")
    with pytest.raises(ValueError, match="missing <feeds>"):
        parse_app_config(str(config_file))


def test_parse_app_config_all_optional_sections_and_absolute_paths(tmp_path):
    feeds_file = tmp_path / "feeds.xml"
    feeds_file.touch()
    embeddings_file = tmp_path / "queries.embeddings.json"
    queries_file = tmp_path / "queries.json"
    config_file = tmp_path / "config.xml"
    config_file.write_text(
        f"""<config>
        <feeds>{feeds_file}</feeds>
        <max-article-length>321</max-article-length>
        <extractor>trafilatura</extractor>
        <concurrency>4</concurrency>
        <pre-filter>
          <enabled>true</enabled>
          <embeddings-path>{embeddings_file.name}</embeddings-path>
          <queries-file>{queries_file.name}</queries-file>
          <cluster-threshold>0.7</cluster-threshold>
        </pre-filter>
        <embeddings><provider>openai</provider><model>embed-model</model></embeddings>
        </config>""",
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))

    assert config.feeds_file == str(feeds_file)
    assert config.pre_filter.enabled is True
    assert config.pre_filter.embeddings_path == str(embeddings_file.resolve())
    assert config.pre_filter.queries_file == str(queries_file.resolve())
    assert config.pre_filter.cluster_threshold == 0.7
    assert config.embeddings.provider == "openai"
    assert config.embeddings.model == "embed-model"
    assert config.max_article_length == 321
    assert config.extractor == "trafilatura"
    assert config.concurrency == 4


def test_parse_app_config_empty_optional_sections_keep_defaults(tmp_path):
    config_file = tmp_path / "config.xml"
    config_file.write_text(
        """<config><feeds>feeds.xml</feeds><pre-filter />
        <logging /></config>""",
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))

    assert config.pre_filter.embeddings_path is None
    assert config.pre_filter.queries_file is None
    assert config.pre_filter.cluster_threshold == 0.8
    assert config.logging.file is None
