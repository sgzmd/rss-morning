import logging
from types import SimpleNamespace

from rss_morning import cli
from rss_morning.config import AppConfig, LoggingConfig, PreFilterConfig, EmailConfig


def test_configure_logging_defaults_to_console_only(monkeypatch, tmp_path):
    original_handlers = list(logging.getLogger().handlers)
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)

    try:
        configure_logging = cli.configure_logging
        configure_logging("INFO")

        handlers = logging.getLogger().handlers
        assert any(isinstance(handler, logging.StreamHandler) for handler in handlers)
        assert not any(isinstance(handler, logging.FileHandler) for handler in handlers)
    finally:
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            logging.getLogger().addHandler(handler)


def test_configure_logging_with_log_file_creates_file_handler(monkeypatch, tmp_path):
    original_handlers = list(logging.getLogger().handlers)
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)

    try:
        log_path = tmp_path / "custom.log"
        cli.configure_logging("INFO", str(log_path))

        assert log_path.exists()
        handlers = logging.getLogger().handlers
        assert any(isinstance(handler, logging.StreamHandler) for handler in handlers)
        assert any(isinstance(handler, logging.FileHandler) for handler in handlers)
    finally:
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            logging.getLogger().addHandler(handler)


def test_main_loads_config_and_runs(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)

    mock_app_config = AppConfig(
        feeds_file="feeds.xml",
        env_file=None,
        limit=10,
        max_age_hours=24,
        summary=False,
        pre_filter=PreFilterConfig(enabled=True, embeddings_path="emb.json"),
        email=EmailConfig(),
        logging=LoggingConfig(),
        max_article_length=1000,
        prompt="System Prompt",
    )

    monkeypatch.setattr(cli, "parse_app_config", lambda path: mock_app_config)
    monkeypatch.setattr(cli, "parse_env_config", lambda path: {})

    captured = {}

    def fake_execute(config):
        captured["config"] = config
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    exit_code = cli.main(["--config", "configs/test.xml"])

    assert exit_code == 0
    run_config = captured["config"]
    assert run_config.limit == 10
    assert run_config.pre_filter is True
    assert run_config.pre_filter_embeddings_path == "emb.json"
    assert run_config.system_prompt == "System Prompt"


def test_main_cli_overrides_logging(monkeypatch):
    captured_log_config = {}

    def fake_configure(level, log_file=None):
        captured_log_config["level"] = level
        captured_log_config["file"] = log_file

    monkeypatch.setattr(cli, "configure_logging", fake_configure)

    mock_app_config = AppConfig(
        feeds_file="feeds.xml",
        env_file=None,
        logging=LoggingConfig(level="INFO", file="config.log"),
    )
    monkeypatch.setattr(cli, "parse_app_config", lambda path: mock_app_config)
    monkeypatch.setattr(cli, "parse_env_config", lambda path: {})
    monkeypatch.setattr(
        cli,
        "execute",
        lambda config: SimpleNamespace(
            output_text="", email_payload=None, is_summary=False
        ),
    )

    cli.main(["--log-level", "DEBUG", "--log-file", "cli.log"])

    assert captured_log_config["level"] == "DEBUG"
    assert captured_log_config["file"] == "cli.log"


def test_main_save_load_articles_args(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)
    mock_app_config = AppConfig(feeds_file="feeds.xml", env_file=None)
    monkeypatch.setattr(cli, "parse_app_config", lambda path: mock_app_config)
    monkeypatch.setattr(cli, "parse_env_config", lambda path: {})

    captured = {}

    def fake_execute(config):
        captured["config"] = config
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    cli.main(["--save-articles", "save.json", "--load-articles", "load.json"])

    assert captured["config"].save_articles_path == "save.json"
    assert captured["config"].load_articles_path == "load.json"
