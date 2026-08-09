"""Shared, bounded HTTP download boundary."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """A recoverable HTTP download failure."""


class ResponseTooLarge(DownloadError):
    """The response exceeded the configured byte budget."""


class UnsupportedContentType(DownloadError):
    """The response content type does not match the caller's policy."""


@dataclass(frozen=True)
class DownloadRequest:
    """Policy for one bounded GET download."""

    url: str
    accepted_content_types: tuple[str, ...] | None = None
    allow_missing_content_type: bool = True


@dataclass(frozen=True)
class DownloadResponse:
    """Normalized result returned by the shared HTTP client."""

    final_url: str
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpClient:
    """Thread-safe client facade backed by one requests session per worker thread."""

    def __init__(
        self,
        *,
        connect_timeout: float = 5,
        read_timeout: float = 20,
        max_bytes: int = 10_485_760,
        retries: int = 2,
        backoff_seconds: float = 0.5,
        per_host_concurrency: int = 2,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("HTTP timeouts must be positive")
        if max_bytes <= 0:
            raise ValueError("HTTP byte limit must be positive")
        if retries < 0 or backoff_seconds < 0:
            raise ValueError("HTTP retry settings cannot be negative")
        if per_host_concurrency <= 0:
            raise ValueError("HTTP per-host concurrency must be positive")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_bytes = max_bytes
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.per_host_concurrency = per_host_concurrency
        self._session_factory = session_factory
        self._local = threading.local()
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._semaphores_lock = threading.Lock()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._session_factory()
            retry = Retry(
                total=self.retries,
                connect=self.retries,
                read=self.retries,
                status=self.retries,
                other=0,
                redirect=0,
                allowed_methods=frozenset({"GET"}),
                status_forcelist={429, 500, 502, 503, 504},
                backoff_factor=self.backoff_seconds,
                backoff_jitter=self.backoff_seconds * 0.1,
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._local.session = session
        return session

    def _host_semaphore(self, hostname: str) -> threading.BoundedSemaphore:
        with self._semaphores_lock:
            semaphore = self._semaphores.get(hostname)
            if semaphore is None:
                semaphore = threading.BoundedSemaphore(self.per_host_concurrency)
                self._semaphores[hostname] = semaphore
            return semaphore

    def get(
        self,
        url: str,
        *,
        accepted_content_types: Sequence[str] | None = None,
        allow_missing_content_type: bool = True,
    ) -> DownloadResponse:
        """Download one GET response with bounded retries, concurrency, and bytes."""
        request = DownloadRequest(
            url=url,
            accepted_content_types=tuple(accepted_content_types)
            if accepted_content_types
            else None,
            allow_missing_content_type=allow_missing_content_type,
        )
        return self.download(request)

    def download(self, request: DownloadRequest) -> DownloadResponse:
        """Execute a typed download request."""
        hostname = urlparse(request.url).hostname
        if not hostname:
            raise DownloadError("Download URL has no hostname")

        semaphore = self._host_semaphore(hostname.lower())
        with semaphore:
            try:
                response = self._session().get(
                    request.url,
                    timeout=(self.connect_timeout, self.read_timeout),
                    stream=True,
                    allow_redirects=True,
                    headers={"User-Agent": "RSS-Morning/1.0"},
                )
                response.raise_for_status()
                headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                content_type = headers.get("content-type", "").split(";", 1)[0].strip()
                if request.accepted_content_types:
                    if not content_type and not request.allow_missing_content_type:
                        raise UnsupportedContentType("Response has no content type")
                    if content_type and not any(
                        content_type == accepted
                        or content_type.startswith(f"{accepted}+")
                        for accepted in request.accepted_content_types
                    ):
                        raise UnsupportedContentType(
                            "Response content type is not accepted"
                        )

                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=65_536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_bytes:
                        response.close()
                        raise ResponseTooLarge("Response exceeded byte limit")
                    chunks.append(chunk)

                return DownloadResponse(
                    final_url=str(response.url),
                    status=int(response.status_code),
                    headers=headers,
                    body=b"".join(chunks),
                )
            except DownloadError:
                raise
            except Exception as exc:  # noqa: BLE001 - requests boundary
                logger.warning("HTTP download failed (%s)", type(exc).__name__)
                raise DownloadError("HTTP download failed") from exc
