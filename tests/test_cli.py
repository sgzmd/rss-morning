import logging

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
