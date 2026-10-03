import textwrap
import pytest

from rss_morning.config import (
    AppConfig,
    load_dotenv,
    parse_app_config,
    parse_env_config,
    parse_feeds_config,
)
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


def test_load_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        textwrap.dedent(
            """
            # Comments should be ignored
            TYPESAFE_API_KEY=test_typesafe_key
            OPENROUTER_API_KEY="test_openrouter_key"
            EMPTY_VAR=
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    loaded = load_dotenv(str(env_file))
    assert loaded["TYPESAFE_API_KEY"] == "test_typesafe_key"
    assert loaded["OPENROUTER_API_KEY"] == "test_openrouter_key"
    assert parse_env_config(str(env_file)) == loaded


def test_load_dotenv_missing_file_returns_empty():
    assert load_dotenv("nonexistent.env") == {}


def test_parse_app_config_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    feeds_file = tmp_path / "feeds.xml"
    feeds_file.touch()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Custom prompt content", encoding="utf-8")

    config_file.write_text(
        textwrap.dedent(
            f"""
            feeds = "feeds.xml"
            limit = 25
            max_age_hours = 12.5
            summary = true
            concurrency = 5
            max_article_length = 300
            prompt_file = "{prompt_file.name}"

            [classification]
            enabled = true
            model = "jev-latest"
            threshold = 0.65

            [email]
            to = "test@example.com"
            from = "mailer@example.com"
            subject = "Custom Digest"

            [logging]
            level = "DEBUG"
            file = "app.log"

            [llm]
            model = "openai/gpt-4o-mini"
            """
        ),
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))

    assert isinstance(config, AppConfig)
    assert config.feeds_file == str(feeds_file.resolve())
    assert config.limit == 25
    assert config.max_age_hours == 12.5
    assert config.summary is True
    assert config.concurrency == 5
    assert config.max_article_length == 300
    assert config.prompt == "Custom prompt content"
    assert config.classification.enabled is True
    assert config.classification.model == "jev-latest"
    assert config.classification.threshold == 0.65
    assert config.email.to_addr == "test@example.com"
    assert config.email.from_addr == "mailer@example.com"
    assert config.email.subject == "Custom Digest"
    assert config.logging.level == "DEBUG"
    assert config.logging.file == str((tmp_path / "app.log").resolve())
    assert config.llm_model == "openai/gpt-4o-mini"


def test_parse_app_config_logging_file_none(tmp_path):
    config_file = tmp_path / "config.toml"
    (tmp_path / "feeds.xml").touch()

    config_file.write_text(
        textwrap.dedent(
            """
            feeds = "feeds.xml"
            [logging]
            level = "INFO"
            file = "none"
            """
        ),
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))
    assert config.logging.level == "INFO"
    assert config.logging.file is None


def test_parse_app_config_inline_prompt(tmp_path):
    config_file = tmp_path / "config.toml"
    (tmp_path / "feeds.xml").touch()

    config_file.write_text(
        textwrap.dedent(
            """
            feeds = "feeds.xml"
            prompt = "Direct inline prompt string"
            """
        ),
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))
    assert config.prompt == "Direct inline prompt string"


def test_parse_app_config_missing_feeds_raises(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("limit = 10", encoding="utf-8")

    with pytest.raises(ValueError, match="Configuration missing required 'feeds'"):
        parse_app_config(str(config_file))


def test_parse_app_config_prompt_file_not_found_raises(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        textwrap.dedent(
            """
            feeds = "feeds.xml"
            prompt_file = "missing_prompt.md"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Prompt file not found"):
        parse_app_config(str(config_file))


def test_parse_app_config_invalid_toml_raises(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("feeds = [unclosed bracket", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to parse TOML configuration"):
        parse_app_config(str(config_file))


def test_parse_app_config_default_areas(tmp_path):
    config_file = tmp_path / "config.toml"
    (tmp_path / "feeds.xml").touch()
    config_file.write_text('feeds = "feeds.xml"\n', encoding="utf-8")

    config = parse_app_config(str(config_file))
    assert len(config.areas) == 6
    assert "mobile_security" in config.areas
    assert config.areas["mobile_security"].threshold == 0.40
    assert "other" in config.areas
    assert config.areas["other"].threshold == 0.75


def test_parse_app_config_custom_areas(tmp_path):
    config_file = tmp_path / "config.toml"
    (tmp_path / "feeds.xml").touch()
    config_file.write_text(
        textwrap.dedent(
            """
            feeds = "feeds.xml"

            [areas.mobile_security]
            label = "Custom Mobile"
            threshold = 0.35
            description = "Custom mobile description"

            [areas.cloud_security]
            label = "Cloud Security"
            threshold = 0.60
            description = "AWS, GCP, Azure security"
            """
        ),
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))
    assert "mobile_security" in config.areas
    assert config.areas["mobile_security"].label == "Custom Mobile"
    assert config.areas["mobile_security"].threshold == 0.35
    assert config.areas["cloud_security"].threshold == 0.60
    # "other" should be automatically retained as fallback
    assert "other" in config.areas
    assert config.areas["other"].threshold == 0.75
