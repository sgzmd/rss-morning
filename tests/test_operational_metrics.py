import json
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from rss_morning.articles import ArticleContent
from rss_morning.emailing import EmailDeliveryResult
from rss_morning.http_client import HttpClient
from rss_morning.metrics import RunStats
from rss_morning.models import FeedConfig, FeedEntry
from rss_morning.prefilter import EmbeddingArticleFilter, _EmbeddingConfig
from rss_morning.runner import RunConfig, execute
from rss_morning.summaries import ProviderError, ProviderResponse, SummarySettings
import rss_morning.runner as runner
import rss_morning.summaries as summaries


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 0.25
        return self.value


def _entry(url: str) -> FeedEntry:
    return FeedEntry(
        link=url,
        category="Fixture",
        title="Public title",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="public metadata",
    )


def test_http_client_reports_page_request_bytes_without_content():
    class Response:
        url = "https://example.invalid/final"
        status_code = 200
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 65_536
            return iter((b"secret ", b"body"))

    session = SimpleNamespace(
        mount=lambda *_args: None,
        get=lambda *_args, **_kwargs: Response(),
    )
    stats = RunStats(clock=Clock())
    client = HttpClient(
        session_factory=lambda: session, stats=stats, page_requests=True
    )

    response = client.get("https://example.invalid/article")

    assert response.body == b"secret body"
    assert stats.http.page_requests == 1
    assert stats.http.transferred_bytes == 11
    assert "secret body" not in stats.render()


def test_summary_metrics_count_planning_retries_splits_cache_and_usage(monkeypatch):
    articles = [
        {"url": f"https://example.invalid/{index}", "category": "A", "text": "x"}
        for index in range(3)
    ]
    stats = RunStats(clock=Clock())
    calls = 0

    def provider(batch, _input_text):
        nonlocal calls
        calls += 1
        if len(batch) > 1:
            raise ProviderError(400)
        if calls == 2:
            raise ProviderError(429)
        item = batch[0]
        payload = {
            "exec-summary": [],
            "summaries": [
                {
                    "url": item["url"],
                    "category": "A",
                    "summary": {
                        "title": "t",
                        "rank-reasoning": "r",
                        "what": "w",
                        "so-what": "s",
                        "now-what": "n",
                    },
                }
            ],
        }
        return ProviderResponse(json.dumps(payload), "fixture/model", 7, 3)

    monkeypatch.setattr(summaries, "estimate_input_tokens", lambda *_args: 5)
    settings = SummarySettings(
        model="fixture/model",
        fallback_models=(),
        max_batch_articles=3,
        max_attempts=2,
        max_split_depth=3,
        cache_enabled=False,
    )

    summaries.generate_summary(
        articles,
        "private prompt",
        settings=settings,
        provider_request=provider,
        sleeper=lambda _delay: None,
        stats=stats,
    )

    assert stats.llm.planned_batches == 1
    assert stats.llm.provider_calls == 6
    assert stats.llm.retries == 1
    assert stats.llm.split_recoveries == 2
    assert stats.llm.exact_cache_hits == 0
    assert stats.llm.submitted_token_estimate > 0
    assert stats.llm.provider_input_tokens == 21
    assert stats.llm.provider_output_tokens == 9

    cached_stats = RunStats(clock=Clock())
    cached_payload = {
        "exec-summary": [],
        "summaries": [
            {
                "url": articles[0]["url"],
                "category": "A",
                "summary": {
                    "title": "t",
                    "rank-reasoning": "r",
                    "what": "w",
                    "so-what": "s",
                    "now-what": "n",
                },
            }
        ],
    }
    summaries.generate_summary(
        articles[:1],
        "private prompt",
        settings=SummarySettings(model="fixture/model"),
        cache_get=lambda _key: json.dumps(cached_payload),
        provider_request=lambda *_args: (_ for _ in ()).throw(
            AssertionError("cached batch must not call provider")
        ),
        stats=cached_stats,
    )
    assert cached_stats.llm.exact_cache_hits == 1
    assert cached_stats.llm.provider_calls == 0


def test_embedding_metrics_include_cache_counts_and_safe_provider_duration(monkeypatch):
    class Backend:
        def embed(self, texts):
            assert texts == ["missing"]
            return [[0.0, 1.0]]

    cached_vector = np.asarray([1.0, 0.0], dtype=np.float32).tobytes()
    monkeypatch.setattr(
        "rss_morning.prefilter.db.get_embeddings_v2",
        lambda *_args: {
            "cached": {"dimension": 2, "vector": cached_vector},
        },
    )
    monkeypatch.setattr(
        "rss_morning.prefilter.db.upsert_embeddings_v2", lambda *_args: None
    )
    stats = RunStats(clock=Clock())
    layer = EmbeddingArticleFilter(
        backend=Backend(),
        queries={},
        config=_EmbeddingConfig(provider="fixture", model="safe-model"),
        session_factory=lambda: nullcontext(object()),
        stats=stats,
    )

    vectors = layer._embed_texts(["cached", "missing"], urls=["cached", "missing"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert stats.embeddings.cache_hits == 1
    assert stats.embeddings.cache_misses == 1
    assert stats.embeddings.provider_calls == 1
    assert stats.embeddings.provider == "fixture"
    assert stats.embeddings.model == "safe-model"
    assert stats.embeddings.duration_seconds == 0.25


def test_execute_reports_mixed_run_metrics_without_changing_stdout_or_leaking(
    monkeypatch, caplog
):
    feeds = [
        FeedConfig("A", "one", "https://feeds.invalid/1"),
        FeedConfig("B", "two", "https://feeds.invalid/2"),
        FeedConfig("C", "bad", "https://feeds.invalid/3"),
    ]
    monkeypatch.setattr(runner, "parse_feeds_config", lambda _path: feeds)

    def fetch_feed(feed, **_kwargs):
        if feed.title == "bad":
            _kwargs["on_result"](False)
            raise RuntimeError("feed unavailable")
        _kwargs["on_result"](True)
        if feed.title == "one":
            return [
                _entry("https://articles.invalid/a"),
                _entry("https://articles.invalid/shared"),
            ]
        return [
            _entry("https://articles.invalid/shared"),
            _entry("https://articles.invalid/b"),
        ]

    monkeypatch.setattr(runner, "fetch_feed_entries", fetch_feed)
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, *_args: entries
    )
    cached = {
        "https://articles.invalid/a": {
            "url": "https://articles.invalid/a",
            "title": "cached",
            "summary": "cached summary",
            "text": "cached text",
            "image": None,
            "published": None,
        },
        "https://articles.invalid/shared": {
            "url": "https://articles.invalid/shared",
            "title": "retry",
            "summary": "retry summary",
            "text": None,
            "image": None,
            "published": None,
        },
    }
    monkeypatch.setattr(runner.db, "init_engine", lambda _connection: object())

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(runner.db, "get_session_factory", lambda _engine: Session)
    monkeypatch.setattr(runner.db, "get_feed_http_states", lambda *_args: {})
    monkeypatch.setattr(runner.db, "get_articles", lambda *_args: cached)
    monkeypatch.setattr(runner.db, "upsert_articles", lambda *_args: None)

    def extract(url, **_kwargs):
        if url.endswith("/b"):
            return ArticleContent(text=None, image=None)
        return ArticleContent(text="PRIVATE ARTICLE BODY", image=None)

    monkeypatch.setattr(runner, "fetch_article_content", extract)
    monkeypatch.setattr(runner, "prepare_tokenizer", lambda: None)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **_kwargs: text)
    stats = RunStats(clock=Clock())
    caplog.set_level(logging.INFO)

    result = execute(
        RunConfig(
            feeds_file="feeds.xml",
            limit=5,
            max_age_hours=None,
            summary=False,
            database_enabled=True,
            database_connection_string="postgresql://PRIVATE-CONNECTION",
        ),
        stats=stats,
    )

    assert json.loads(result.output_text) == result.email_payload
    assert result.stats is stats
    assert (stats.feeds.configured, stats.feeds.successful, stats.feeds.failed) == (
        3,
        2,
        1,
    )
    assert (stats.entries.before_deduplication, stats.entries.after_deduplication) == (
        4,
        3,
    )
    assert stats.articles.cache_hits == 1
    assert stats.articles.cache_misses == 2
    assert stats.articles.retried_null_content == 1
    assert stats.articles.successful_extractions == 1
    assert stats.articles.extraction_failures == 1
    assert stats.durations.feed_seconds > 0
    assert stats.durations.extraction_seconds > 0
    assert stats.durations.total_seconds > 0
    for private in (
        "PRIVATE ARTICLE BODY",
        "PRIVATE-CONNECTION",
        "PRIVATE-API-KEY",
        "PRIVATE PROMPT",
        "PRIVATE EMAIL BODY",
    ):
        assert private not in caplog.text


def test_execute_collects_prefilter_llm_email_metrics_and_survives_metric_logging_failure(
    monkeypatch, tmp_path
):
    articles = [
        {"url": "https://example.invalid/1", "category": "A", "text": "PRIVATE"},
        {"url": "https://example.invalid/2", "category": "A", "text": "PRIVATE"},
    ]
    snapshot = tmp_path / "articles.json"
    snapshot.write_text(json.dumps(articles), encoding="utf-8")

    class Filter:
        def filter(self, incoming, **_kwargs):
            return [
                {
                    **incoming[0],
                    "other_urls": [{"url": incoming[1]["url"], "distance": 0.1}],
                }
            ]

    monkeypatch.setattr(runner, "_create_prefilter", lambda *_args: Filter())

    def generate(incoming, _prompt, *, stats, **_kwargs):
        stats.record_llm(
            planned_batches=1,
            provider_calls=1,
            exact_cache_hits=1,
            submitted_token_estimate=12,
        )
        payload = {"summaries": []}
        return json.dumps(payload), payload

    monkeypatch.setattr(runner, "generate_summary", generate)
    monkeypatch.setattr(
        runner,
        "send_email_report",
        lambda **_kwargs: EmailDeliveryResult(attempted=True, sent=False, failed=True),
    )
    stats = RunStats(clock=Clock())
    monkeypatch.setattr(
        stats, "render", lambda: (_ for _ in ()).throw(RuntimeError("metrics broken"))
    )

    result = execute(
        RunConfig(
            feeds_file="unused.xml",
            limit=1,
            max_age_hours=None,
            summary=True,
            pre_filter=True,
            load_articles_path=str(snapshot),
            system_prompt="PRIVATE PROMPT",
            email_to="to@example.invalid",
        ),
        stats=stats,
    )

    assert json.loads(result.output_text) == {"summaries": []}
    assert stats.prefilter.input_articles == 2
    assert stats.prefilter.full_text_candidates == 2
    assert stats.prefilter.representatives == 1
    assert stats.prefilter.clustered_duplicates == 1
    assert stats.llm.planned_batches == 1
    assert stats.email.attempted == 1
    assert stats.email.sent == 0
    assert stats.email.failed == 1
    assert stats.durations.prefilter_seconds > 0
    assert stats.durations.summary_seconds > 0
    assert stats.durations.email_seconds > 0

    broken = RunStats(clock=Clock())
    monkeypatch.setattr(
        broken, "finish", lambda: (_ for _ in ()).throw(RuntimeError("metrics broken"))
    )
    runner._finish_run_metrics(broken)


def test_metadata_first_candidate_count_is_recorded(monkeypatch):
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [FeedConfig("A", "feed", "https://feed.invalid")],
    )

    def fetch_feed(_feed, **kwargs):
        kwargs["on_result"](True)
        return [
            _entry("https://article.invalid/1"),
            _entry("https://article.invalid/2"),
        ]

    monkeypatch.setattr(runner, "fetch_feed_entries", fetch_feed)
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda entries, *_args: entries
    )
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: ArticleContent(text="safe", image=None),
    )
    monkeypatch.setattr(runner, "prepare_tokenizer", lambda: None)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **_kwargs: text)

    class Filter:
        def select_metadata_candidates(self, incoming, **_kwargs):
            return incoming[:1]

        def filter(self, incoming, **_kwargs):
            return incoming

    monkeypatch.setattr(runner, "_create_prefilter", lambda *_args: Filter())
    stats = RunStats(clock=Clock())

    execute(
        RunConfig(
            feeds_file="feeds.xml",
            limit=2,
            max_age_hours=None,
            summary=False,
            pre_filter=True,
            pre_filter_mode="metadata-first",
        ),
        stats=stats,
    )

    assert stats.prefilter.input_articles == 2
    assert stats.prefilter.metadata_candidates == 1
    assert stats.prefilter.full_text_candidates == 1


def test_run_stats_stage_context_records_duration():
    stats = RunStats(clock=Clock())

    with stats.stage("feed"):
        pass

    assert stats.durations.feed_seconds == 0.25
