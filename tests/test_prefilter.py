import random

import numpy as np

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


def test_filter_uses_embedding_backend():
    backend = FakeEmbeddingBackend(
        {
            ("custom query",): [[1.0, 0.0]],
            ("Article A", "Article B"): [[1.0, 0.0], [0.0, 1.0]],
        }
    )
    filt = EmbeddingArticleFilter(
        backend=backend,
        queries=("custom query",),
    )

    articles = [
        {"title": "Article A", "url": "https://example.com/a"},
        {"title": "Article B", "url": "https://example.com/b"},
    ]

    filtered = filt.filter(articles)

    assert [article["title"] for article in filtered] == ["Article A"]
    assert filtered[0]["prefilter_match"] == "custom query"
    assert filtered[0]["prefilter_score"] == 1.0
    assert backend.calls == [
        ("custom query",),
        ("Article A", "Article B"),
    ]


def test_filter_clusters_articles():
    query_vector = np.array([1.0, 1.0], dtype=float)
    query_vector /= np.linalg.norm(query_vector)
    article_a = np.array([1.0, 0.0])
    article_b = np.array([0.99, 0.01], dtype=float)
    article_b /= np.linalg.norm(article_b)
    article_c = np.array([0.0, 1.0])

    backend = FakeEmbeddingBackend(
        {
            ("cluster query",): [query_vector.tolist()],
            ("Article A", "Article B", "Article C"): [
                article_a.tolist(),
                article_b.tolist(),
                article_c.tolist(),
            ],
        }
    )

    filt = EmbeddingArticleFilter(
        backend=backend,
        queries=("cluster query",),
    )

    articles = [
        {"title": "Article A", "url": "https://example.com/a"},
        {"title": "Article B", "url": "https://example.com/b"},
        {"title": "Article C", "url": "https://example.com/c"},
    ]

    filtered = filt.filter(
        articles,
        cluster_threshold=0.98,
        rng=random.Random(0),
    )

    assert [article["url"] for article in filtered] == [
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert filtered[0]["other_urls"] == [
        {"url": "https://example.com/a", "distance": 0.0001}
    ]
    assert filtered[1]["other_urls"] == []


def test_filter_respects_cluster_threshold():
    query_vector = np.array([1.0, 0.0])
    article_a = np.array([1.0, 0.0])
    article_b = np.array([0.99, 0.01], dtype=float)
    article_b /= np.linalg.norm(article_b)

    backend = FakeEmbeddingBackend(
        {
            ("threshold query",): [query_vector.tolist()],
            ("Article A", "Article B"): [
                article_a.tolist(),
                article_b.tolist(),
            ],
        }
    )

    filt = EmbeddingArticleFilter(
        backend=backend,
        queries=("threshold query",),
    )

    articles = [
        {"title": "Article A", "url": "https://example.com/a"},
        {"title": "Article B", "url": "https://example.com/b"},
    ]

    filtered = filt.filter(
        articles,
        cluster_threshold=0.99999,
        rng=random.Random(1),
    )

    assert [article["url"] for article in filtered] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert filtered[0]["other_urls"] == []
    assert filtered[1]["other_urls"] == []
