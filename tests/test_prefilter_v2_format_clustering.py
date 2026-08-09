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


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_cluster_threshold_rejects_out_of_range_values(value):
    filt = prefilter.EmbeddingArticleFilter(backend=MappingBackend({}), queries={})
    with pytest.raises(ValueError, match="cluster_threshold"):
        filt.filter([{"url": "u"}], cluster_threshold=value)
