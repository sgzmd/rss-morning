import textwrap
from rss_morning.config import parse_app_config, parse_env_config, AppConfig


def test_parse_env_config(tmp_path):
    env_file = tmp_path / "env.xml"
    env_file.write_text(
        textwrap.dedent("""
            <environment>
                <variable name="TEST_VAR">test_value</variable>
                <variable name="ANOTHER_VAR">12345</variable>
            </environment>
        """),
        encoding="utf-8",
    )

    env_vars = parse_env_config(str(env_file))
    assert env_vars["TEST_VAR"] == "test_value"
    assert env_vars["ANOTHER_VAR"] == "12345"


def test_parse_app_config(tmp_path):
    config_file = tmp_path / "config.xml"
    feeds_file = tmp_path / "feeds.xml"
    env_file = tmp_path / "env.xml"
    log_file = tmp_path / "app.log"

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("System Prompt content", encoding="utf-8")

    # Create dummy sibling files
    feeds_file.touch()
    env_file.touch()

    config_file.write_text(
        textwrap.dedent(f"""
            <config>
                <feeds>feeds.xml</feeds>
                <env>env.xml</env>
                <limit>25</limit>
                <max-age-hours>12.5</max-age-hours>
                <summary>true</summary>
                <email>
                    <to>test@example.com</to>
                    <from>sender@example.com</from>
                    <subject>Test Subject</subject>
                </email>
                <logging>
                    <level>DEBUG</level>
                    <file>app.log</file>
                </logging>
                <prompt file="{prompt_file.name}" />
            </config>
        """),
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))

    assert isinstance(config, AppConfig)
    assert config.limit == 25
    assert config.max_age_hours == 12.5
    assert config.summary is True
    assert config.email.to_addr == "test@example.com"
    assert config.logging.level == "DEBUG"
    # Check path resolution (should be absolute)
    assert config.feeds_file == str(feeds_file.resolve())
    assert config.env_file == str(env_file.resolve())
    assert config.logging.file == str(log_file.resolve())
    assert config.prompt.strip() == "System Prompt content"


def test_parse_app_config_minimal(tmp_path):
    config_file = tmp_path / "minimal.xml"
    feeds_file = tmp_path / "feeds.xml"
    feeds_file.touch()

    config_file.write_text(
        textwrap.dedent("""
            <config>
                <feeds>feeds.xml</feeds>
            </config>
        """),
        encoding="utf-8",
    )

    config = parse_app_config(str(config_file))
    assert config.limit == 10
    assert config.summary is False
    assert config.env_file is None
    assert config.prompt is None
