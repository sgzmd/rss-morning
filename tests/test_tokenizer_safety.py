"""Token truncation must remain deterministic and offline-safe."""

import concurrent.futures
import importlib
import logging
import socket
import threading

import pytest

from rss_morning import articles


class CharacterEncoder:
    def encode(self, value):
        return list(value)

    def decode(self, tokens):
        return "".join(tokens)


@pytest.fixture(autouse=True)
def fresh_articles_module():
    global articles
    articles = importlib.import_module("rss_morning.articles")
    importlib.reload(articles)


def test_encoder_initializes_once_for_repeated_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        articles.tiktoken,
        "get_encoding",
        lambda name: calls.append(name) or CharacterEncoder(),
    )

    assert [articles.truncate_text("abcdef", 3) for _ in range(5)] == ["abc"] * 5
    assert calls == ["cl100k_base"]


def test_concurrent_truncation_initializes_encoder_once(monkeypatch):
    calls = []
    first_inside = threading.Event()
    second_attempt = threading.Event()
    real_lock = threading.Lock()

    class CoordinatedLock:
        entrants = 0

        def __enter__(self):
            self.entrants += 1
            if self.entrants == 1:
                real_lock.acquire()
                first_inside.set()
                assert second_attempt.wait(timeout=5)
            else:
                second_attempt.set()
                real_lock.acquire()
            return self

        def __exit__(self, *_args):
            real_lock.release()

    monkeypatch.setattr(articles, "_encoder_lock", CoordinatedLock())
    monkeypatch.setattr(
        articles.tiktoken,
        "get_encoding",
        lambda name: calls.append(name) or CharacterEncoder(),
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(articles.truncate_text, "abcdef", 3)
        assert first_inside.wait(timeout=5)
        second = executor.submit(articles.truncate_text, "abcdef", 3)
        results = [first.result(), second.result()]

    assert results == ["abc", "abc"]
    assert calls == ["cl100k_base"]


def test_encoder_failure_uses_fallback_and_logs_once_without_content(
    monkeypatch, caplog
):
    private_text = "private-article-content"

    def fail(_name):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(articles.tiktoken, "get_encoding", fail)
    caplog.set_level(logging.WARNING, logger="rss_morning.articles")

    assert articles.truncate_text(private_text, 7) == "private"
    assert articles.truncate_text(private_text, 7) == "private"
    assert caplog.text.count("offline character fallback") == 1
    assert "RuntimeError" in caplog.text
    assert private_text not in caplog.text
    assert "network unavailable" not in caplog.text


def test_truncate_text_rejects_none_at_boundary():
    with pytest.raises(TypeError, match="text must be a string"):
        articles.truncate_text(None, 10)  # type: ignore[arg-type]


def test_unicode_fallback_is_deterministic(monkeypatch):
    monkeypatch.setattr(
        articles.tiktoken,
        "get_encoding",
        lambda _name: (_ for _ in ()).throw(OSError("offline")),
    )

    assert articles.truncate_text("🙂é漢字", 3) == "🙂é漢"
    assert articles.truncate_text("🙂é", 3) == "🙂é"


def test_truncate_text_rejects_nonpositive_direct_limit():
    with pytest.raises(ValueError, match="limit must be positive"):
        articles.truncate_text("text", 0)


def test_offline_smoke_does_not_open_socket(monkeypatch):
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("socket must not be used")
        ),
    )
    monkeypatch.setattr(
        articles.tiktoken,
        "get_encoding",
        lambda _name: (_ for _ in ()).throw(OSError("cache unavailable")),
    )

    assert articles.truncate_text("offline text", 7) == "offline"
