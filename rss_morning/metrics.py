"""Private, content-free operational metrics for one digest run."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator

logger = logging.getLogger(__name__)


@dataclass
class FeedMetrics:
    configured: int = 0
    successful: int = 0
    failed: int = 0


@dataclass
class EntryMetrics:
    before_deduplication: int = 0
    after_deduplication: int = 0


@dataclass
class ArticleMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    retried_null_content: int = 0
    successful_extractions: int = 0
    extraction_failures: int = 0


@dataclass
class HttpMetrics:
    page_requests: int = 0
    transferred_bytes: int = 0


@dataclass
class PrefilterMetrics:
    input_articles: int = 0
    metadata_candidates: int = 0
    full_text_candidates: int = 0
    representatives: int = 0
    clustered_duplicates: int = 0


@dataclass
class EmbeddingMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    provider_calls: int = 0
    provider: str = ""
    model: str = ""
    duration_seconds: float = 0.0


@dataclass
class LlmMetrics:
    planned_batches: int = 0
    provider_calls: int = 0
    retries: int = 0
    split_recoveries: int = 0
    exact_cache_hits: int = 0
    submitted_token_estimate: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0


@dataclass
class EmailMetrics:
    attempted: int = 0
    sent: int = 0
    failed: int = 0


@dataclass
class DurationMetrics:
    feed_seconds: float = 0.0
    extraction_seconds: float = 0.0
    prefilter_seconds: float = 0.0
    summary_seconds: float = 0.0
    email_seconds: float = 0.0
    total_seconds: float = 0.0


@dataclass
class RunStats:
    """Thread-safe, content-free counters and stage durations for one run."""

    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    feeds: FeedMetrics = field(default_factory=FeedMetrics)
    entries: EntryMetrics = field(default_factory=EntryMetrics)
    articles: ArticleMetrics = field(default_factory=ArticleMetrics)
    http: HttpMetrics = field(default_factory=HttpMetrics)
    prefilter: PrefilterMetrics = field(default_factory=PrefilterMetrics)
    embeddings: EmbeddingMetrics = field(default_factory=EmbeddingMetrics)
    llm: LlmMetrics = field(default_factory=LlmMetrics)
    email: EmailMetrics = field(default_factory=EmailMetrics)
    durations: DurationMetrics = field(default_factory=DurationMetrics)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _started: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._started = self.clock()

    def add(self, section: str, **values: int | float | str) -> None:
        """Add numeric values and replace safe string dimensions."""
        with self._lock:
            target = getattr(self, section)
            for name, value in values.items():
                current = getattr(target, name)
                if isinstance(value, str):
                    setattr(target, name, value)
                else:
                    setattr(target, name, current + value)

    def set(self, section: str, **values: int | float | str) -> None:
        """Set absolute counter values."""
        with self._lock:
            target = getattr(self, section)
            for name, value in values.items():
                setattr(target, name, value)

    def record_llm(self, **values: int) -> None:
        self.add("llm", **values)

    def start(self) -> float:
        return self.clock()

    def complete_stage(self, name: str, started: float) -> None:
        elapsed = max(0.0, self.clock() - started)
        self.add("durations", **{f"{name}_seconds": elapsed})
        logger.info("Operational stage complete: stage=%s seconds=%.3f", name, elapsed)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = self.clock()
        try:
            yield
        finally:
            self.complete_stage(name, started)

    def finish(self) -> None:
        self.set("durations", total_seconds=max(0.0, self.clock() - self._started))

    def render(self) -> str:
        """Return a concise JSON-like representation containing no run content."""
        payload = {
            name: asdict(getattr(self, name))
            for name in (
                "feeds",
                "entries",
                "articles",
                "http",
                "prefilter",
                "embeddings",
                "llm",
                "email",
                "durations",
            )
        }
        return repr(payload)

    def emit(self) -> None:
        """Log the final metrics without allowing telemetry to fail a run."""
        try:
            logger.info("Operational run summary: %s", self.render())
        except Exception:  # noqa: BLE001 - metrics must never fail the digest
            logger.warning("Operational metrics summary unavailable")
