"""Behavior contract for opt-in metadata-first page selection."""

from datetime import datetime, timezone

import pytest

from rss_morning import config as config_module
from rss_morning import runner
from rss_morning.articles import ArticleContent
from rss_morning.models import FeedConfig, FeedEntry
from rss_morning.prefilter import EmbeddingArticleFilter, _EmbeddingConfig


def entry(url, title=None):
    return FeedEntry(
        link=url,
        category="Original",
        title=title or url,
        summary=f"summary {url}",
        published=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


def runtime_config(**overrides):
    values = dict(
        feeds_file="feeds.xml",
        limit=10,
        max_age_hours=None,
        summary=False,
        pre_filter=True,
    )
    values.update(overrides)
    return runner.RunConfig(**values)


def install_feed_fixture(monkeypatch, entries):
    monkeypatch.setattr(
        runner,
        "parse_feeds_config",
        lambda _path: [FeedConfig("Original", "Feed", "feed")],
    )
    monkeypatch.setattr(
        runner, "fetch_feed_entries", lambda _feed, **_kwargs: list(entries)
    )
    monkeypatch.setattr(
        runner, "select_recent_entries", lambda values, _limit, _cutoff: values
    )
    monkeypatch.setattr(runner, "prepare_tokenizer", lambda: None)
    monkeypatch.setattr(runner, "truncate_text", lambda text, **_kwargs: text)


def test_config_defaults_full_text_and_validates_metadata_mode(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text("<config><feeds>feeds.xml</feeds></config>")
    assert config_module.parse_app_config(str(path)).pre_filter.mode == "full-text"

    path.write_text(
        """<config><feeds>feeds.xml</feeds><pre-filter>
        <mode>metadata-first</mode><candidate-multiplier>4</candidate-multiplier>
        </pre-filter></config>"""
    )
    parsed = config_module.parse_app_config(str(path))
    assert parsed.pre_filter.mode == "metadata-first"
    assert parsed.pre_filter.candidate_multiplier == 4

    for mode, multiplier in (("invalid", "3"), ("metadata-first", "0")):
        path.write_text(
            f"<config><feeds>feeds.xml</feeds><pre-filter><mode>{mode}</mode>"
            f"<candidate-multiplier>{multiplier}</candidate-multiplier>"
            "</pre-filter></config>"
        )
        with pytest.raises(ValueError):
            config_module.parse_app_config(str(path))


def test_full_text_mode_downloads_every_deduplicated_entry(monkeypatch):
    install_feed_fixture(monkeypatch, [entry("one"), entry("two"), entry("one")])
    calls = []
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **_kwargs: calls.append(url) or ArticleContent(url, None),
    )

    output = runner._collect_entries(
        runtime_config(pre_filter_mode="full-text", concurrency=1)
    )

    assert calls == ["one", "two"]
    assert [item["url"] for item in output] == ["one", "two"]


def test_metadata_first_rejections_skip_pages_and_deduplicate_before_embedding(
    monkeypatch,
):
    install_feed_fixture(
        monkeypatch, [entry("one"), entry("two"), entry("one"), entry("three")]
    )
    seen = []

    class Selector:
        def select_metadata_candidates(self, articles, *, candidate_multiplier):
            seen.extend(articles)
            assert candidate_multiplier == 3
            return [articles[0], articles[2]]

    monkeypatch.setattr(runner, "_create_prefilter", lambda *_args: Selector())
    calls = []
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **_kwargs: calls.append(url) or ArticleContent(url, None),
    )

    output = runner._collect_entries(
        runtime_config(
            pre_filter_mode="metadata-first", candidate_multiplier=3, concurrency=1
        )
    )

    assert [item["url"] for item in seen] == ["one", "two", "three"]
    assert calls == ["one", "three"]
    assert [item["url"] for item in output] == ["one", "three"]


def test_metadata_stage_uses_cached_full_text_without_page_download(
    monkeypatch, tmp_path
):
    install_feed_fixture(monkeypatch, [entry("cached"), entry("rejected")])
    engine = runner.db.init_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    factory = runner.db.get_session_factory(engine)
    with factory() as session:
        runner.db.upsert_article(
            session, {"url": "cached", "title": "Cached", "text": "full cached text"}
        )

    class Selector:
        def select_metadata_candidates(self, articles, **_kwargs):
            cached = next(item for item in articles if item["url"] == "cached")
            assert cached["text"] == "full cached text"
            return [cached]

    monkeypatch.setattr(runner, "_create_prefilter", lambda *_args: Selector())
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda *_args, **_kwargs: pytest.fail("no selected page needs downloading"),
    )

    result = runner._collect_entries(
        runtime_config(pre_filter_mode="metadata-first"), session_factory=factory
    )
    assert [item["url"] for item in result] == ["cached"]
    engine.dispose()


def test_first_stage_failure_falls_back_to_full_text_downloads(monkeypatch):
    install_feed_fixture(monkeypatch, [entry("one"), entry("two")])

    class FailingSelector:
        def select_metadata_candidates(self, *_args, **_kwargs):
            raise RuntimeError("metadata embeddings failed")

    monkeypatch.setattr(runner, "_create_prefilter", lambda *_args: FailingSelector())
    calls = []
    monkeypatch.setattr(
        runner,
        "fetch_article_content",
        lambda url, **_kwargs: calls.append(url) or ArticleContent(url, None),
    )

    runner._collect_entries(
        runtime_config(pre_filter_mode="metadata-first", concurrency=1)
    )
    assert calls == ["one", "two"]


class Backend:
    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, texts):
        return [self.vectors[text] for text in texts]


def test_candidate_multiplier_applies_per_category():
    vectors = {"qa": [1.0, 0.0], "qb": [0.0, 1.0]}
    articles = []
    for category, prefix, vector in (("A", "a", [1.0, 0.0]), ("B", "b", [0.0, 1.0])):
        for index in range(5):
            title = f"{prefix}{index}"
            vectors[title] = vector
            articles.append({"url": title, "title": title, "category": category})
    filt = EmbeddingArticleFilter(
        backend=Backend(vectors),
        queries={"A": ("qa",), "B": ("qb",)},
        config=_EmbeddingConfig(max_cluster_size=2),
    )

    selected = filt.select_metadata_candidates(articles, candidate_multiplier=2)

    assert len(selected) == 8
    assert {item["prefilter_match"] for item in selected} == {"A", "B"}
    with pytest.raises(ValueError, match="candidate_multiplier"):
        filt.select_metadata_candidates(articles, candidate_multiplier=0)


@pytest.mark.parametrize("mode", ["full-text", "metadata-first"])
def test_snapshot_replay_never_collects_or_downloads(monkeypatch, tmp_path, mode):
    snapshot = tmp_path / "articles.json"
    snapshot.write_text("[]")
    monkeypatch.setattr(
        runner,
        "_collect_entries",
        lambda *_args, **_kwargs: pytest.fail("snapshot replay must not fetch"),
    )

    result = runner.execute(
        runtime_config(
            pre_filter=False,
            pre_filter_mode=mode,
            load_articles_path=str(snapshot),
        )
    )
    assert result.email_payload == []
