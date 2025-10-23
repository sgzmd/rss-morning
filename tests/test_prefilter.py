import json
from types import SimpleNamespace

import pytest

from rss_morning.prefilter import (
    EmbeddingArticleFilter,
    _EmbeddingConfig,
    export_security_query_embeddings,
)


class FakeEmbeddingsAPI:
    def __init__(self, vector_map):
        self.vector_map = vector_map
        self.calls = []

    def create(self, *, model, input):
        self.calls.append({"model": model, "input": list(input)})
        data = [SimpleNamespace(embedding=self.vector_map[text]) for text in input]
        return SimpleNamespace(data=data)


class FakeClient:
    def __init__(self, vector_map):
        self.embeddings = FakeEmbeddingsAPI(vector_map)


def reset_cache():
    EmbeddingArticleFilter._cached_query_key = None
    EmbeddingArticleFilter._cached_query_embeddings = None


def test_embedding_filter_retains_above_threshold(monkeypatch):
    vector_map = {
        "QueryA": [1.0, 0.0],
        "QueryB": [0.0, 1.0],
        "Match Title\nMatch Summary\nMatch Body": [1.0, 0.0],
        "Miss Title\nMiss Body": [0.3, 0.9539392014169457],
    }

    monkeypatch.setattr(EmbeddingArticleFilter, "QUERIES", ("QueryA", "QueryB"))
    monkeypatch.setattr(
        EmbeddingArticleFilter,
        "CONFIG",
        _EmbeddingConfig(model="fake-model", batch_size=2, threshold=0.98),
    )
    reset_cache()

    filter_layer = EmbeddingArticleFilter(client=FakeClient(vector_map))

    articles = [
        {"title": "Match Title", "summary": "Match Summary", "text": "Match Body"},
        {"title": "Miss Title", "summary": "", "text": "Miss Body"},
    ]

    retained = filter_layer.filter(articles)

    assert len(retained) == 1
    assert retained[0]["title"] == "Match Title"
    assert retained[0]["prefilter_match"] == "QueryA"
    assert retained[0]["prefilter_score"] == pytest.approx(1.0)

    calls = filter_layer._client.embeddings.calls
    assert calls[0]["input"] == ["QueryA", "QueryB"]
    assert calls[1]["input"] == [
        "Match Title\nMatch Summary\nMatch Body",
        "Miss Title\nMiss Body",
    ]


def test_embedding_filter_returns_original_on_error(monkeypatch):
    filter_layer = EmbeddingArticleFilter(client=FakeClient({"noop": [1.0]}))

    def boom(*args, **kwargs):
        raise RuntimeError("no embeddings for you")

    monkeypatch.setattr(filter_layer, "_get_query_embeddings", boom)

    articles = [{"title": "Title", "summary": "Sum", "text": "Body"}]
    retained = filter_layer.filter(articles)

    assert retained == [articles[0]]


def test_embedding_filter_uses_precomputed_queries(tmp_path, monkeypatch):
    monkeypatch.setattr(EmbeddingArticleFilter, "QUERIES", ("QueryA", "QueryB"))
    reset_cache()
    cache_file = tmp_path / "queries.json"
    cache_file.write_text(
        json.dumps(
            {
                "model": EmbeddingArticleFilter.CONFIG.model,
                "threshold": EmbeddingArticleFilter.CONFIG.threshold,
                "queries": ["QueryA", "QueryB"],
                "embeddings": [[1.0, 0.0], [0.0, 1.0]],
            }
        )
    )

    article_text = "Title\nBody"
    vector_map = {
        article_text: [1.0, 0.0],
    }
    client = FakeClient(vector_map)

    filter_layer = EmbeddingArticleFilter(
        client=client, query_embeddings_path=str(cache_file)
    )

    articles = [{"title": "Title", "summary": "", "text": "Body"}]
    retained = filter_layer.filter(articles)

    assert len(retained) == 1
    assert client.embeddings.calls[0]["input"] == [article_text]


def test_export_security_query_embeddings(tmp_path, monkeypatch):
    monkeypatch.setattr(EmbeddingArticleFilter, "QUERIES", ("Alpha", "Beta"))
    config = _EmbeddingConfig(model="fake", batch_size=2, threshold=0.5)
    reset_cache()

    vector_map = {
        "Alpha": [1.0, 0.0],
        "Beta": [0.0, 1.0],
    }
    client = FakeClient(vector_map)

    destination = tmp_path / "export.json"
    export_security_query_embeddings(str(destination), config=config, client=client)

    payload = json.loads(destination.read_text())
    assert payload["model"] == "fake"
    assert payload["threshold"] == 0.5
    assert payload["queries"] == ["Alpha", "Beta"]
    assert payload["embeddings"] == [[1.0, 0.0], [0.0, 1.0]]

    calls = client.embeddings.calls
    assert calls[0]["input"] == ["Alpha", "Beta"]
