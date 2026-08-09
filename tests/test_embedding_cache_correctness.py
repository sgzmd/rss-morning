"""Behavior contract for versioned, content-correct embedding caches."""

from __future__ import annotations

import numpy as np
import pytest

from rss_morning import db, prefilter


class Backend:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        if callable(self.vectors):
            return self.vectors(texts)
        return [list(vector) for vector in self.vectors]


class ObjectSession:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return None


@pytest.fixture
def session_factory():
    engine = db.init_engine("sqlite:///:memory:")
    factory = db.get_session_factory(engine)
    yield factory
    engine.dispose()


def make_filter(backend, session_factory, *, provider="fastembed", model="shared"):
    return prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={},
        session_factory=session_factory,
        config=prefilter._EmbeddingConfig(provider=provider, model=model),
    )


def test_unchanged_input_hits_but_content_provider_and_version_changes_miss(
    session_factory, monkeypatch
):
    first = Backend([[1.0, 0.0]])
    assert make_filter(first, session_factory)._embed_texts(["same"], ["url"]) == [
        [1.0, 0.0]
    ]

    unchanged = Backend(lambda _texts: pytest.fail("unchanged input must hit cache"))
    assert make_filter(unchanged, session_factory)._embed_texts(["same"], ["url"]) == [
        [1.0, 0.0]
    ]

    for changed_text in ("new title", "new summary", "new body"):
        changed = Backend([[0.0, 1.0]])
        make_filter(changed, session_factory)._embed_texts([changed_text], ["url"])
        assert changed.calls == [(changed_text,)]

    provider_changed = Backend([[0.5, 0.5]])
    make_filter(provider_changed, session_factory, provider="openai")._embed_texts(
        ["same"], ["url"]
    )
    assert provider_changed.calls == [("same",)]

    monkeypatch.setattr(prefilter, "PREPROCESSING_VERSION", "test-version-2")
    version_changed = Backend([[0.25, 0.75]])
    make_filter(version_changed, session_factory)._embed_texts(["same"], ["url"])
    assert version_changed.calls == [("same",)]


def test_query_centroid_cache_includes_provider_identity():
    prefilter.EmbeddingArticleFilter._cached_centroids.clear()
    first = Backend([[1.0, 0.0]])
    second = Backend([[0.0, 1.0]])
    prefilter.EmbeddingArticleFilter(
        backend=first,
        queries={"A": ("query",)},
        config=prefilter._EmbeddingConfig(provider="fastembed", model="same"),
    )._get_category_centroids()
    result = prefilter.EmbeddingArticleFilter(
        backend=second,
        queries={"A": ("query",)},
        config=prefilter._EmbeddingConfig(provider="openai", model="same"),
    )._get_category_centroids()

    assert second.calls == [("query",)]
    assert np.array_equal(result["A"], np.array([0.0, 1.0]))


@pytest.mark.parametrize("vectors", [[], [[1.0], [2.0]]])
def test_backend_vector_count_mismatch_is_typed_and_filter_fails_open(vectors):
    backend = Backend(lambda texts: [[1.0]] if texts == ["query"] else vectors)
    filt = prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={"A": ("query",)},
        config=prefilter._EmbeddingConfig(model=f"count-{len(vectors)}"),
    )
    articles = [{"url": "u", "title": "article", "category": "Original"}]

    with pytest.raises(prefilter.EmbeddingValidationError):
        filt._embed_texts(["one"], ["u"])
    assert filt.filter(articles) == articles
    assert articles == [{"url": "u", "title": "article", "category": "Original"}]


def test_dimension_mismatch_is_rejected_instead_of_zip_truncation():
    with pytest.raises(prefilter.EmbeddingValidationError, match="dimension"):
        prefilter.EmbeddingArticleFilter._dot(np.array([1.0, 2.0]), np.array([1.0]))


def test_corrupt_vector_is_ignored_replaced_and_order_is_restored(monkeypatch):
    backend = Backend([[2.0, 0.0], [3.0, 0.0]])
    filt = prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={},
        session_factory=ObjectSession,
    )
    monkeypatch.setattr(
        prefilter.db,
        "get_embeddings_v2",
        lambda *_args: {
            "cached": {
                "input_hash": "ignored",
                "dimension": 2,
                "vector": np.array([1.0, 0.0], dtype=np.float32).tobytes(),
            },
            "corrupt": {"input_hash": "ignored", "dimension": 3, "vector": b"bad"},
        },
    )
    saved = []
    monkeypatch.setattr(
        prefilter.db,
        "upsert_embeddings_v2",
        lambda _session, records: saved.extend(records),
    )
    monkeypatch.setattr(prefilter, "_embedding_input_hash", lambda _text: "ignored")

    vectors = filt._embed_texts(
        ["new text", "cached text", "corrupt text"],
        ["new", "cached", "corrupt"],
    )

    assert vectors == [[2.0, 0.0], [1.0, 0.0], [3.0, 0.0]]
    assert [record["url"] for record in saved] == ["new", "corrupt"]


def test_zero_vectors_are_deterministic_and_late_failure_returns_pristine_articles(
    monkeypatch,
):
    backend = Backend(
        lambda texts: [[1.0, 0.0]] if texts == ["query"] else [[0.0, 0.0], [1.0, 0.0]]
    )
    filt = prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={"Matched": ("query",)},
        config=prefilter._EmbeddingConfig(model="late", threshold=-1.0),
    )
    articles = [
        {"url": "zero", "title": "zero", "category": "Original"},
        {"url": "one", "title": "one", "category": "Original"},
    ]
    monkeypatch.setattr(
        filt, "_cosine", lambda *_args: (_ for _ in ()).throw(RuntimeError("late"))
    )

    assert filt.filter(articles) == articles
    assert all(set(article) == {"url", "title", "category"} for article in articles)
