import logging
from types import SimpleNamespace

from rss_morning import cli
from rss_morning.cli import configure_logging


def test_configure_logging_creates_file_and_console_handlers(monkeypatch, tmp_path):
    original_handlers = list(logging.getLogger().handlers)
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)

    try:
        monkeypatch.chdir(tmp_path)

        configure_logging("INFO")

        log_files = list(tmp_path.glob("rss-morning-*.log"))
        assert len(log_files) == 1

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
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)

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
    assert config.cluster_threshold == 0.84


def test_main_enables_pre_filter_without_path(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)

    def fake_execute(config):
        assert config.pre_filter is True
        assert config.pre_filter_embeddings_path is None
        assert config.cluster_threshold == 0.84
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    exit_code = cli.main(["--pre-filter"])
    assert exit_code == 0


def test_main_overrides_cluster_threshold(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)

    captured = {}

    def fake_execute(config):
        captured["threshold"] = config.cluster_threshold
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr(cli, "execute", fake_execute)

    exit_code = cli.main(["--cluster-threshold", "0.9"])
    assert exit_code == 0
    assert captured["threshold"] == 0.9
