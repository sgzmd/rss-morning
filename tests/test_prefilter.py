import json

import numpy as np
import pytest

from rss_morning import prefilter
from rss_morning.prefilter import EmbeddingArticleFilter


class FakeEmbeddingBackend:
    def __init__(self, responses):
        self._responses = {tuple(key): value for key, value in responses.items()}
        self.calls = []

    def embed(self, texts):
        key = tuple(texts)
        self.calls.append(key)
        if key not in self._responses:
            raise AssertionError(f"No fake embedding configured for {key!r}")
        return [list(vector) for vector in self._responses[key]]


def test_filter_uses_embedding_backend_with_categories():
    # Centroid for "Category A" will be [1.0, 0.0]
    # Centroid for "Category B" will be [0.0, 1.0]

    backend = FakeEmbeddingBackend(
        {
            ("cat A query",): [[1.0, 0.0]],
            ("cat B query",): [[0.0, 1.0]],
            ("Article A", "Article B"): [[1.0, 0.0], [0.0, 1.0]],
        }
    )
    filt = EmbeddingArticleFilter(
        backend=backend,
        queries={"Category A": ("cat A query",), "Category B": ("cat B query",)},
    )

    articles = [
        {"title": "Article A", "url": "https://example.com/a"},
        {"title": "Article B", "url": "https://example.com/b"},
    ]

    filtered = filt.filter(articles)

    assert len(filtered) == 2
    # Article A should match Category A
    a_art = next(a for a in filtered if a["title"] == "Article A")
    assert a_art["category"] == "Category A"
    assert a_art["prefilter_score"] == 1.0

    # Article B should match Category B
    b_art = next(a for a in filtered if a["title"] == "Article B")
    assert b_art["category"] == "Category B"
    assert b_art["prefilter_score"] == 1.0


def test_filter_enforces_max_cluster_size():
    # 6 articles matching Category A
    # Centroid A: [1.0, 0.0]
    # Articles with decreasing similarity to [1.0, 0.0]
    # We'll use 1D approx on [1.0, 0.0] vs close vectors

    # Let's say we have vectors:
    # 1. [1.0, 0.0] (Score 1.0)
    # 2. [0.99, 0.01ish] (Score 0.99)
    # ...
    # We mock them directly

    titles = [f"Art{i}" for i in range(10)]
    vectors = []
    # Create vectors with score = 1.0 - i*0.01
    for i in range(10):
        # We cheat and just say score will be X.
        # But we need dot product.
        val = 1.0 - (i * 0.01)
        # Vector = [val, sqrt(1-val^2)]
        y = np.sqrt(1 - val**2)
        vectors.append([val, y])

    backend = FakeEmbeddingBackend(
        {
            ("query",): [[1.0, 0.0]],
            tuple(titles): vectors,
        }
    )

    config = type(EmbeddingArticleFilter.CONFIG)(max_cluster_size=3)

    filt = EmbeddingArticleFilter(
        backend=backend, queries={"Category A": ("query",)}, config=config
    )

    articles = [{"title": t, "url": f"http://{t}"} for t in titles]

    filtered = filt.filter(articles)

    # Should only keep top 3
    assert len(filtered) == 3
    assert [a["title"] for a in filtered] == ["Art0", "Art1", "Art2"]

    # Without an explicit clustering threshold, representatives remain separate.
    top_art = filtered[0]
    assert top_art["other_urls"] == []

    # Others should have empty other_urls because we only attach to Kernel?
    # Wait, my implementation attached to Kernel, but returned all kept items.
    # So Art1 and Art2 are in the list.
    assert filtered[1]["other_urls"] == []


def test_filter_discards_below_threshold():
    backend = FakeEmbeddingBackend(
        {
            ("query",): [[1.0, 0.0]],
            ("Article Bad",): [[0.0, 1.0]],  # Orthogonal, score 0
        }
    )

    config = type(EmbeddingArticleFilter.CONFIG)(threshold=0.5)

    filt = EmbeddingArticleFilter(
        backend=backend, queries={"Category A": ("query",)}, config=config
    )

    filtered = filt.filter([{"title": "Article Bad", "url": "bad"}])
    assert len(filtered) == 0


def test_compose_article_text_truncates_long_content():
    """Verify that article content is truncated to the configured limit."""
    long_text = "x" * 10000
    article = {"title": "Title", "summary": "Summary", "text": long_text}

    # Default limit (5000)
    backend = FakeEmbeddingBackend({})
    filt = EmbeddingArticleFilter(backend=backend)
    composed = filt._compose_article_text(article)
    assert len(composed) == 5000

    # Custom limit via config
    config_cls = type(EmbeddingArticleFilter.CONFIG)
    custom_config = config_cls(max_article_length=100)
    filt_custom = EmbeddingArticleFilter(config=custom_config, backend=backend)
    composed_custom = filt_custom._compose_article_text(article)
    assert len(composed_custom) == 100


def test_load_queries_supports_text_json_and_errors(tmp_path, monkeypatch):
    text_file = tmp_path / "queries.txt"
    text_file.write_text("# comment\n first \n\nsecond\n", encoding="utf-8")
    assert prefilter.load_queries(str(text_file)) == {"General": ("first", "second")}

    dict_file = tmp_path / "queries.json"
    dict_file.write_text(
        json.dumps({"A": [" one ", ""], "ignored": "not-list"}), encoding="utf-8"
    )
    assert prefilter.load_queries(str(dict_file)) == {"A": ("one",)}

    dict_file.write_text(json.dumps([" one ", ""]), encoding="utf-8")
    assert prefilter.load_queries(str(dict_file)) == {"General": ("one",)}

    dict_file.write_text("42", encoding="utf-8")
    assert prefilter.load_queries(str(dict_file)) == {"General": ("42",)}

    dict_file.write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        prefilter.load_queries(str(dict_file))
    with pytest.raises(FileNotFoundError):
        prefilter.load_queries(str(tmp_path / "missing.txt"))

    text_file.unlink()
    dict_file.unlink()
    monkeypatch.setattr(prefilter, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(prefilter, "DEFAULT_QUERIES_FILE", tmp_path / "queries.txt")
    monkeypatch.setattr(prefilter, "EXAMPLE_QUERIES_FILE", tmp_path / "example.txt")
    with pytest.raises(RuntimeError, match="No queries file found"):
        prefilter.load_queries()


def test_load_queries_default_search_skips_bad_candidate(tmp_path, monkeypatch):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "queries.json").write_text("bad json", encoding="utf-8")
    example = tmp_path / "example.txt"
    example.write_text("fallback", encoding="utf-8")
    monkeypatch.setattr(prefilter, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(prefilter, "DEFAULT_QUERIES_FILE", tmp_path / "queries.txt")
    monkeypatch.setattr(prefilter, "EXAMPLE_QUERIES_FILE", example)

    assert prefilter.load_queries() == {"General": ("fallback",)}


def test_constructor_validation_and_backend_selection(monkeypatch, tmp_path, caplog):
    backend = FakeEmbeddingBackend({})
    with pytest.raises(ValueError, match="either queries or queries_file"):
        EmbeddingArticleFilter(
            backend=backend, queries={"A": ["q"]}, queries_file="queries.txt"
        )

    queries_file = tmp_path / "queries.txt"
    queries_file.write_text("query", encoding="utf-8")
    assert EmbeddingArticleFilter(
        backend=backend, queries_file=str(queries_file)
    ).queries == {"General": ("query",)}

    caplog.set_level("INFO")
    EmbeddingArticleFilter(client=object(), backend=backend, queries={})
    assert "backend takes precedence" in caplog.text

    created = []
    monkeypatch.setattr(
        prefilter,
        "FastEmbedBackend",
        lambda **kwargs: created.append(("fast", kwargs)) or backend,
    )
    EmbeddingArticleFilter(queries={}, config=prefilter._EmbeddingConfig())
    assert created[-1][0] == "fast"

    monkeypatch.setattr(
        prefilter,
        "OpenAIEmbeddingBackend",
        lambda **kwargs: created.append(("openai", kwargs)) or backend,
    )
    client = object()
    config = prefilter._EmbeddingConfig(provider="openai")
    EmbeddingArticleFilter(client=client, queries={}, config=config)
    assert created[-1][1]["client"] is client
    monkeypatch.setattr(prefilter, "OpenAI", lambda: client)
    EmbeddingArticleFilter(queries={}, config=config)
    assert created[-1][1]["client"] is client


def test_filter_fail_open_empty_centroids_embeddings_and_exceptions():
    article = {"title": "Article", "url": "url"}
    empty_queries = EmbeddingArticleFilter(
        backend=FakeEmbeddingBackend({}), queries={"Empty": ()}
    )
    assert empty_queries.filter([]) == []
    assert empty_queries.filter([article]) == [article]

    no_vectors = EmbeddingArticleFilter(
        backend=FakeEmbeddingBackend({("q",): [[1.0]], ("Article",): []}),
        queries={"A": ("q",)},
        config=prefilter._EmbeddingConfig(model="no-vectors"),
    )
    assert no_vectors.filter([article]) == [article]

    failing = EmbeddingArticleFilter(
        backend=FakeEmbeddingBackend({}),
        queries={"A": ("missing",)},
        config=prefilter._EmbeddingConfig(model="failure"),
    )
    assert failing.filter([article]) == [article]


def test_filter_handles_zero_vectors_and_zero_cluster_size():
    backend = FakeEmbeddingBackend({("q",): [[1.0, 0.0]], ("Article",): [[0.0, 0.0]]})
    filt = EmbeddingArticleFilter(
        backend=backend,
        queries={"A": ("q",)},
        config=prefilter._EmbeddingConfig(
            model="zero-article", threshold=-1.0, max_cluster_size=0
        ),
    )
    assert filt.filter([{"title": "Article", "url": "url"}]) == []


def test_centroids_cache_empty_queries_and_zero_mean():
    EmbeddingArticleFilter._cached_centroids.clear()
    backend = FakeEmbeddingBackend({("q",): [[0.0, 0.0]]})
    filt = EmbeddingArticleFilter(
        backend=backend,
        queries={"Empty": (), "Zero": ("q",)},
        config=prefilter._EmbeddingConfig(model="zero-centroid"),
    )
    first = filt._get_category_centroids()
    second = filt._get_category_centroids()
    assert first == second
    assert np.array_equal(first["Zero"], np.array([0.0, 0.0]))
    assert backend.calls == [("q",)]


class SessionContext:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return None


def test_embed_texts_uses_valid_cache_and_replaces_invalid_cache(monkeypatch):
    backend = FakeEmbeddingBackend({("bad text", "new text"): [[0.2], [0.3]]})
    filt = EmbeddingArticleFilter(
        backend=backend,
        queries={},
        session_factory=SessionContext,
        config=prefilter._EmbeddingConfig(model="cached-model"),
    )
    monkeypatch.setattr(
        prefilter.db,
        "get_embeddings_v2",
        lambda *_args: {
            "cached": {
                "dimension": 1,
                "vector": np.array([0.1], dtype=np.float32).tobytes(),
            },
            "bad": {"dimension": 1, "vector": b"bad"},
        },
    )
    saved = []
    monkeypatch.setattr(
        prefilter.db,
        "upsert_embeddings_v2",
        lambda _session, records: saved.extend(records),
    )

    vectors = filt._embed_texts(
        ["cached text", "bad text", "new text"], ["cached", "bad", "new"]
    )

    assert np.allclose(vectors, [[0.1], [0.2], [0.3]])
    assert [record["url"] for record in saved] == ["bad", "new"]


def test_embed_texts_all_cached_and_missing_provider_result(monkeypatch):
    backend = FakeEmbeddingBackend({("missing",): []})
    filt = EmbeddingArticleFilter(
        backend=backend,
        queries={},
        session_factory=SessionContext,
        config=prefilter._EmbeddingConfig(model="cache-branches"),
    )
    monkeypatch.setattr(
        prefilter.db,
        "get_embeddings_v2",
        lambda *_args: {
            "cached": {
                "dimension": 1,
                "vector": np.array([0.1], dtype=np.float32).tobytes(),
            }
        },
    )
    monkeypatch.setattr(prefilter.db, "upsert_embeddings_v2", lambda *_args: None)
    assert np.allclose(filt._embed_texts(["cached"], ["cached"]), [[0.1]])
    with pytest.raises(prefilter.EmbeddingValidationError, match="count"):
        filt._embed_texts(["missing"], ["new"])


def test_scoring_helpers_other_urls_and_cosine_bounds(monkeypatch):
    filt = EmbeddingArticleFilter(backend=FakeEmbeddingBackend({}), queries={})
    assert np.isnan(filt._score_against_centroids(np.array([1.0]), {})[1])
    assert filt._dot(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == 11.0
    assert filt._cosine(np.array([0.0]), np.array([1.0])) == 0.0

    scored = EmbeddingArticleFilter._ScoredArticle
    kernel = scored(1.0, {"url": "kernel"}, np.array([1.0, 0.0]), "A")
    near = scored(0.9, {"url": "near"}, np.array([1.0, 0.0]), "A")
    far = scored(0.8, {}, np.array([0.0, 1.0]), "A")
    assert filt._build_other_urls(kernel, []) == []
    assert filt._build_other_urls(kernel, [far, near], limit=1) == [
        {"url": "near", "distance": 0.0}
    ]
    assert filt._build_other_urls(kernel, [far]) == [{"url": "", "distance": 1.0}]

    monkeypatch.setattr(prefilter.np.linalg, "norm", lambda _vector: 1.0)
    monkeypatch.setattr(prefilter.np, "dot", lambda _left, _right: 2.0)
    assert filt._cosine(np.array([1.0]), np.array([1.0])) == 1.0
    monkeypatch.setattr(prefilter.np, "dot", lambda _left, _right: -2.0)
    assert filt._cosine(np.array([1.0]), np.array([1.0])) == -1.0


def test_export_security_query_embeddings_writes_payload(tmp_path, monkeypatch):
    class FakeFilter:
        queries = {"A": ("query",)}

        def __init__(self, **_kwargs):
            pass

        def _embed_texts(self, texts):
            assert texts == ["query"]
            return [[1.0]]

    monkeypatch.setattr(prefilter, "EmbeddingArticleFilter", FakeFilter)
    output = tmp_path / "vectors.json"
    config = prefilter._EmbeddingConfig(model="model", threshold=0.7)

    assert (
        prefilter.export_security_query_embeddings(
            str(output), config=config, queries={"A": ("query",)}
        )
        == output
    )
    assert json.loads(output.read_text()) == {
        "format_version": 2,
        "provider": "fastembed",
        "model": "model",
        "preprocessing_version": prefilter.PREPROCESSING_VERSION,
        "dimension": 1,
        "entries": [{"category": "A", "query": "query", "vector": [1.0]}],
    }
