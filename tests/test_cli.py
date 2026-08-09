import json
import logging
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_shim
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


def test_main_does_not_log_prompt_or_database_credentials(monkeypatch, caplog):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)
    mock_app_config = AppConfig(
        feeds_file="feeds.xml",
        env_file=None,
        prompt="private prompt instructions",
    )
    mock_app_config.database.enabled = True
    mock_app_config.database.connection_string = (
        "postgresql://user:secret@example.com/rss"
    )
    monkeypatch.setattr(cli, "parse_app_config", lambda path: mock_app_config)
    monkeypatch.setattr(
        cli,
        "execute",
        lambda config: SimpleNamespace(
            output_text="{}", email_payload=None, is_summary=False
        ),
    )

    caplog.set_level("INFO")
    assert cli.main([]) == 0

    assert "Active Configuration" in caplog.text
    assert "private prompt instructions" not in caplog.text
    assert "postgresql://user:secret@example.com/rss" not in caplog.text
    assert "***MASKED***" in caplog.text


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


def test_configure_logging_rejects_invalid_level_and_creates_parent(tmp_path):
    with pytest.raises(ValueError, match="Unsupported log level"):
        cli.configure_logging("invalid")

    original_handlers = list(logging.getLogger().handlers)
    try:
        log_path = tmp_path / "nested" / "app.log"
        cli.configure_logging("DEBUG", str(log_path))
        assert log_path.exists()
    finally:
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            logging.getLogger().addHandler(handler)


def test_main_loads_environment_and_honors_stdout_logging(monkeypatch):
    app_config = AppConfig(
        feeds_file="feeds.xml",
        env_file="env.xml",
        logging=LoggingConfig(level="WARNING", file="ignored.log"),
    )
    monkeypatch.setattr(cli, "parse_app_config", lambda _path: app_config)
    monkeypatch.setattr(cli, "parse_env_config", lambda _path: {"LOADED_ENV": "yes"})
    captured = {}
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda level, log_file=None: captured.update(level=level, file=log_file),
    )
    monkeypatch.setattr(
        cli,
        "execute",
        lambda _config: SimpleNamespace(
            output_text="[]", email_payload=[], is_summary=False
        ),
    )
    monkeypatch.setenv("RSS_MORNING_LOG_STDOUT", "1")

    assert cli.main(["--log-file", "cli.log", "--llm-dry-run"]) == 0
    assert captured == {"level": "WARNING", "file": None}
    assert cli.os.environ["LOADED_ENV"] == "yes"


def test_main_send_email_from_json(monkeypatch, tmp_path):
    payload_path = tmp_path / "summary.json"
    payload_path.write_text(json.dumps({"summaries": []}), encoding="utf-8")
    app_config = AppConfig(feeds_file="feeds.xml", env_file=None)
    app_config.email = EmailConfig(
        to_addr="to@example.com", from_addr="from@example.com", subject="Subject"
    )
    monkeypatch.setattr(cli, "parse_app_config", lambda _path: app_config)
    monkeypatch.setattr(cli, "configure_logging", lambda *_args, **_kwargs: None)
    calls = []
    from rss_morning import emailing

    monkeypatch.setattr(
        emailing, "send_email_report", lambda **kwargs: calls.append(kwargs)
    )

    assert cli.main(["--send-email-from-json", str(payload_path)]) == 0
    assert calls == [
        {
            "payload": {"summaries": []},
            "is_summary": True,
            "to_address": "to@example.com",
            "from_address": "from@example.com",
            "subject": "Subject",
        }
    ]


def test_main_error_paths(monkeypatch):
    monkeypatch.setattr(
        cli,
        "parse_app_config",
        lambda _path: (_ for _ in ()).throw(ValueError("bad config")),
    )
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2

    for error in (RuntimeError("runtime"), FileNotFoundError("missing")):
        monkeypatch.setattr(
            cli,
            "parse_app_config",
            lambda _path, error=error: (_ for _ in ()).throw(error),
        )
        assert cli.main([]) == 1

    monkeypatch.setattr(
        cli,
        "parse_app_config",
        lambda _path: (_ for _ in ()).throw(TypeError("unexpected")),
    )
    assert cli.main([]) == 1


@pytest.mark.parametrize("arguments", [[], ["--log-level", "INFO"]])
def test_main_module_entrypoint_adds_debug_only_when_absent(monkeypatch, arguments):
    assert main_shim.main is not None
    calls = []
    monkeypatch.setattr(cli, "main", lambda: calls.append(list(sys.argv)) or 0)
    monkeypatch.setattr(sys, "argv", ["main.py", *arguments])

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(__file__).parents[1] / "main.py"), run_name="__main__")

    assert exc.value.code == 0
    if arguments:
        assert calls[0][-2:] == ["--log-level", "INFO"]
    else:
        assert calls[0][-2:] == ["--log-level", "DEBUG"]
