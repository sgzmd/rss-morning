"""Contract for query-vector format v2 and representative clustering."""

import json

import pytest

from rss_morning import prefilter


class MappingBackend:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        return [self.vectors[text] for text in texts]


def test_export_flattens_actual_queries_with_complete_v2_metadata(tmp_path):
    backend = MappingBackend({"one": [1.0, 0.0], "two": [0.0, 1.0]})
    output = tmp_path / "queries.json"
    config = prefilter._EmbeddingConfig(provider="fastembed", model="model")

    prefilter.export_security_query_embeddings(
        str(output),
        config=config,
        queries={"A": ("one",), "B": ("two",)},
        backend=backend,
    )

    payload = json.loads(output.read_text())
    assert backend.calls == [("one", "two")]
    assert payload == {
        "format_version": 2,
        "provider": "fastembed",
        "model": "model",
        "preprocessing_version": prefilter.PREPROCESSING_VERSION,
        "dimension": 2,
        "entries": [
            {"category": "A", "query": "one", "vector": [1.0, 0.0]},
            {"category": "B", "query": "two", "vector": [0.0, 1.0]},
        ],
    }
    assert list(tmp_path.iterdir()) == [output]


def test_export_failure_preserves_existing_file_and_removes_temporary(
    tmp_path, monkeypatch
):
    output = tmp_path / "queries.json"
    output.write_text("original")
    backend = MappingBackend({"one": [1.0]})

    def fail_dump(_payload, stream, **_kwargs):
        stream.write("partial")
        raise RuntimeError("write failed")

    monkeypatch.setattr(prefilter.json, "dump", fail_dump)
    with pytest.raises(RuntimeError, match="write failed"):
        prefilter.export_security_query_embeddings(
            str(output), queries={"A": ("one",)}, backend=backend
        )

    assert output.read_text() == "original"
    assert list(tmp_path.iterdir()) == [output]


def compatible_payload():
    return {
        "format_version": 2,
        "provider": "fastembed",
        "model": "model",
        "preprocessing_version": prefilter.PREPROCESSING_VERSION,
        "dimension": 2,
        "entries": [
            {"category": "A", "query": "one", "vector": [1.0, 0.0]},
            {"category": "A", "query": "two", "vector": [0.0, 1.0]},
        ],
    }


def test_matching_precomputed_file_avoids_query_embedding_calls(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(compatible_payload()))
    backend = MappingBackend({})
    filt = prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={"A": ("one", "two")},
        query_embeddings_path=str(path),
        config=prefilter._EmbeddingConfig(model="model"),
    )

    assert list(filt._get_category_centroids()) == ["A"]
    assert backend.calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(provider="openai"),
        lambda p: p.update(model="stale"),
        lambda p: p.update(preprocessing_version="old"),
        lambda p: p["entries"].pop(),
        lambda p: p["entries"].append(dict(p["entries"][0])),
        lambda p: p["entries"][0].update(vector=["bad", 0.0]),
        lambda p: p.update(dimension=3),
        lambda p: p.update(dimension=0),
        lambda p: p.update(entries=None),
    ],
)
def test_stale_or_corrupt_precomputed_file_recomputes_safely(
    tmp_path, mutation, caplog
):
    prefilter.EmbeddingArticleFilter._cached_centroids.clear()
    payload = compatible_payload()
    mutation(payload)
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(payload))
    backend = MappingBackend({"one": [1.0, 0.0], "two": [0.0, 1.0]})
    filt = prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={"A": ("one", "two")},
        query_embeddings_path=str(path),
        config=prefilter._EmbeddingConfig(model="model"),
    )

    assert list(filt._get_category_centroids()) == ["A"]
    assert backend.calls == [("one", "two")]
    assert "precomputed query embeddings" in caplog.text.lower()


def test_unreadable_optional_query_file_warns_and_recomputes(tmp_path, caplog):
    prefilter.EmbeddingArticleFilter._cached_centroids.clear()
    backend = MappingBackend({"one": [1.0]})
    filt = prefilter.EmbeddingArticleFilter(
        backend=backend,
        queries={"A": ("one",)},
        query_embeddings_path=str(tmp_path / "missing.json"),
        config=prefilter._EmbeddingConfig(model="read-failure"),
    )
    assert list(filt._get_category_centroids()) == ["A"]
    assert backend.calls == [("one",)]
    assert "precomputed query embeddings" in caplog.text.lower()


def test_greedy_clustering_returns_deterministic_representatives_and_duplicates():
    vectors = {
        "query": [1.0, 0.0],
        "high": [1.0, 0.0],
        "duplicate": [0.99, 0.1],
        "different": [0.8, 0.6],
    }
    articles = [
        {"url": "duplicate", "title": "duplicate", "published": "2025-01-02"},
        {"url": "different", "title": "different", "published": "2025-01-01"},
        {"url": "high", "title": "high", "published": "2025-01-03"},
    ]
    filt = prefilter.EmbeddingArticleFilter(
        backend=MappingBackend(vectors),
        queries={"A": ("query",)},
        config=prefilter._EmbeddingConfig(model="cluster", max_cluster_size=2),
    )

    result = filt.filter(reversed(articles), cluster_threshold=0.95)

    assert [item["url"] for item in result] == ["high", "different"]
    assert result[0]["other_urls"] == [{"url": "duplicate", "distance": 0.0051}]
    assert result[1]["other_urls"] == []


def test_clustering_ties_use_publication_then_url_and_boundaries_are_valid():
    vectors = {
        "query": [1.0, 0.0],
        "old": [1.0, 0.0],
        "z-new": [1.0, 0.0],
        "a-new": [1.0, 0.0],
        "different": [0.0, 1.0],
    }
    articles = [
        {"url": "old", "title": "old", "published": "2025-01-01"},
        {"url": "z-new", "title": "z-new", "published": "2025-01-02"},
        {"url": "a-new", "title": "a-new", "published": "2025-01-02"},
        {"url": "different", "title": "different", "published": "2025-01-03"},
    ]
    config = prefilter._EmbeddingConfig(
        model="ties", threshold=-1.0, max_cluster_size=4
    )

    tied = prefilter.EmbeddingArticleFilter(
        backend=MappingBackend(vectors), queries={"A": ("query",)}, config=config
    ).filter(articles, cluster_threshold=1)
    assert [item["url"] for item in tied] == ["a-new", "different"]
    assert [item["url"] for item in tied[0]["other_urls"]] == ["z-new", "old"]

    all_duplicates = prefilter.EmbeddingArticleFilter(
        backend=MappingBackend(vectors), queries={"A": ("query",)}, config=config
    ).filter(articles, cluster_threshold=0)
    assert [item["url"] for item in all_duplicates] == ["a-new"]


def test_duplicate_attaches_to_nearest_of_multiple_representatives():
    vectors = {
        "query": [1.0, 1.0],
        "a": [1.0, 0.0],
        "b": [0.0, 1.0],
        "near-a": [0.8, -0.2],
        "near-b": [-0.2, 0.8],
    }
    articles = [{"url": url, "title": url} for url in ("near-b", "b", "near-a", "a")]
    result = prefilter.EmbeddingArticleFilter(
        backend=MappingBackend(vectors),
        queries={"A": ("query",)},
        config=prefilter._EmbeddingConfig(
            model="nearest", threshold=-1.0, max_cluster_size=2
        ),
    ).filter(articles, cluster_threshold=0.9)

    assert [item["url"] for item in result] == ["a", "b"]
    assert [item["url"] for item in result[0]["other_urls"]] == ["near-a"]
    assert [item["url"] for item in result[1]["other_urls"]] == ["near-b"]


def test_export_failure_before_temporary_creation_preserves_destination(
    tmp_path, monkeypatch
):
    output = tmp_path / "queries.json"
    output.write_text("original")
    monkeypatch.setattr(
        prefilter.tempfile,
        "NamedTemporaryFile",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("cannot create")),
    )
    with pytest.raises(RuntimeError, match="cannot create"):
        prefilter.export_security_query_embeddings(
            str(output),
            queries={"A": ("one",)},
            backend=MappingBackend({"one": [1.0]}),
        )
    assert output.read_text() == "original"


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_cluster_threshold_rejects_out_of_range_values(value):
    filt = prefilter.EmbeddingArticleFilter(backend=MappingBackend({}), queries={})
    with pytest.raises(ValueError, match="cluster_threshold"):
        filt.filter([{"url": "u"}], cluster_threshold=value)
