"""Behavior contract for the shared bounded HTTP download client."""

import concurrent.futures
import importlib
import logging
import threading

import pytest


def http_client_module():
    try:
        return importlib.import_module("rss_morning.http_client")
    except ModuleNotFoundError:
        pytest.fail("rss_morning.http_client must provide the shared download boundary")


class FakeResponse:
    def __init__(
        self,
        chunks=(b"body",),
        *,
        status=200,
        url="https://example.com/final",
        headers=None,
    ):
        self._chunks = chunks
        self.status_code = status
        self.url = url
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.mounts = []
        self.calls = []

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def make_client(session, **overrides):
    module = http_client_module()
    options = {
        "connect_timeout": 5,
        "read_timeout": 20,
        "max_bytes": 100,
        "retries": 2,
        "backoff_seconds": 0.5,
        "per_host_concurrency": 2,
        "session_factory": lambda: session,
    }
    options.update(overrides)
    return module.HttpClient(**options)


def test_request_uses_timeouts_streaming_redirects_and_final_url():
    response = FakeResponse(url="https://example.com/redirected")
    session = FakeSession(response)
    client = make_client(session)

    result = client.get("https://example.com/start")

    assert session.calls == [
        (
            "https://example.com/start",
            {
                "timeout": (5, 20),
                "stream": True,
                "allow_redirects": True,
                "headers": {"User-Agent": "RSS-Morning/1.0"},
            },
        )
    ]
    assert result.final_url == "https://example.com/redirected"
    assert result.body == b"body"
    assert result.status == 200
    assert result.headers["content-type"] == "text/html; charset=utf-8"


def test_retry_policy_is_bounded_to_safe_get_transients_and_retry_after():
    session = FakeSession()
    make_client(session)

    retries = session.mounts[0][1].max_retries
    assert retries.total == 2
    assert retries.connect == 2
    assert retries.read == 2
    assert retries.allowed_methods == frozenset({"GET"})
    assert retries.status_forcelist == {429, 500, 502, 503, 504}
    assert 400 not in retries.status_forcelist
    assert 404 not in retries.status_forcelist
    assert retries.respect_retry_after_header is True
    assert retries.backoff_factor == 0.5


def test_oversized_response_is_closed_and_raises_typed_error():
    module = http_client_module()
    response = FakeResponse(chunks=(b"1234", b"5678"))
    client = make_client(FakeSession(response), max_bytes=6)

    with pytest.raises(module.ResponseTooLarge):
        client.get("https://example.com/large")

    assert response.closed is True


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "application/pdf"}])
def test_content_type_policy_rejects_missing_or_unacceptable_type(headers):
    module = http_client_module()
    client = make_client(FakeSession(FakeResponse(headers=headers)))

    with pytest.raises(module.UnsupportedContentType):
        client.get(
            "https://example.com/content",
            accepted_content_types=("text/html",),
            allow_missing_content_type=False,
        )


def test_caller_can_allow_missing_content_type():
    client = make_client(FakeSession(FakeResponse(headers={})))
    assert (
        client.get(
            "https://example.com/content",
            accepted_content_types=("text/html",),
            allow_missing_content_type=True,
        ).body
        == b"body"
    )


def test_sessions_are_reused_per_thread_but_not_shared_between_threads():
    sessions = []

    def factory():
        session = FakeSession()
        sessions.append(session)
        return session

    module = http_client_module()
    client = module.HttpClient(session_factory=factory)
    client.get("https://example.com/one")
    client.get("https://example.com/two")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(client.get, "https://example.com/thread").result()

    assert len(sessions) == 2
    assert len(sessions[0].calls) == 2
    assert len(sessions[1].calls) == 1


class ConcurrencySession(FakeSession):
    def __init__(self, probe):
        super().__init__()
        self.probe = probe

    def get(self, url, **kwargs):
        with self.probe():
            return super().get(url, **kwargs)


class Probe:
    def __init__(self, participants):
        self.barrier = threading.Barrier(participants)
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def __call__(self):
        probe = self

        class Context:
            def __enter__(self):
                with probe.lock:
                    probe.active += 1
                    probe.peak = max(probe.peak, probe.active)
                probe.barrier.wait(timeout=5)

            def __exit__(self, *_args):
                with probe.lock:
                    probe.active -= 1

        return Context()


def test_per_host_concurrency_is_bounded():
    probe = Probe(2)
    module = http_client_module()
    client = module.HttpClient(
        per_host_concurrency=2,
        session_factory=lambda: ConcurrencySession(probe),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                client.get, [f"https://example.com/{index}" for index in range(4)]
            )
        )

    assert len(results) == 4
    assert probe.peak == 2


def test_different_hosts_progress_independently():
    probe = Probe(2)
    module = http_client_module()
    client = module.HttpClient(
        per_host_concurrency=1,
        session_factory=lambda: ConcurrencySession(probe),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                client.get,
                ["https://one.example/path", "https://two.example/path"],
            )
        )

    assert len(results) == 2
    assert probe.peak == 2


def test_download_error_logs_neither_body_nor_authorization(monkeypatch, caplog):
    module = http_client_module()
    session = FakeSession()

    def fail(_url, **_kwargs):
        raise RuntimeError("secret-response-body Bearer secret-token")

    monkeypatch.setattr(session, "get", fail)
    client = make_client(session)
    caplog.set_level(logging.WARNING, logger="rss_morning.http_client")

    with pytest.raises(module.DownloadError):
        client.get("https://example.com/fail")

    assert "secret-response-body" not in caplog.text
    assert "secret-token" not in caplog.text
