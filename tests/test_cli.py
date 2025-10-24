import logging
from types import SimpleNamespace

from rss_morning import cli
from rss_morning.cli import configure_logging


def test_configure_logging_defaults_to_console_only(monkeypatch, tmp_path):
    original_handlers = list(logging.getLogger().handlers)
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)

    try:
        monkeypatch.chdir(tmp_path)

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

        configure_logging("INFO", str(log_path))

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


def test_cli_parser_accepts_pre_filter_path():
    parser = cli.build_parser()
    args = parser.parse_args(["--pre-filter", "cache.json"])
    assert args.pre_filter == "cache.json"


def test_main_passes_pre_filter_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)

    captured = {}

    def fake_execute(config):
        captured["config"] = config
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    cache_path = tmp_path / "queries.json"

    exit_code = cli.main(["--pre-filter", str(cache_path)])

    assert exit_code == 0
    config = captured["config"]
    assert config.pre_filter is True
    assert config.pre_filter_embeddings_path == str(cache_path)
    assert config.cluster_threshold == 0.8


def test_main_enables_pre_filter_without_path(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)

    def fake_execute(config):
        assert config.pre_filter is True
        assert config.pre_filter_embeddings_path is None
        assert config.cluster_threshold == 0.8
        assert config.save_articles_path is None
        assert config.load_articles_path is None
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    exit_code = cli.main(["--pre-filter"])
    assert exit_code == 0


def test_main_overrides_cluster_threshold(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)

    captured = {}

    def fake_execute(config):
        captured["threshold"] = config.cluster_threshold
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    exit_code = cli.main(["--cluster-threshold", "0.9"])
    assert exit_code == 0
    assert captured["threshold"] == 0.9


def test_main_handles_save_and_load(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "configure_logging", lambda level, log_file=None: None)

    captured = {}

    def fake_execute(config):
        captured["config"] = config
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    save_path = tmp_path / "stored.json"
    load_path = tmp_path / "input.json"

    exit_code = cli.main(
        [
            "--save-articles",
            str(save_path),
            "--load-articles",
            str(load_path),
        ]
    )

    assert exit_code == 0
    config = captured["config"]
    assert config.save_articles_path == str(save_path)
    assert config.load_articles_path == str(load_path)
