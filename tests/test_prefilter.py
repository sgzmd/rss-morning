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

    # Check that Top 1 has 'other_urls' populated
    top_art = filtered[0]
    assert len(top_art["other_urls"]) == 2  # The other 2 kept articles
    assert top_art["other_urls"][0]["url"] == "http://Art1"

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
