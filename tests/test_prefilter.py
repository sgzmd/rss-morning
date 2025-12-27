import pytest

from rss_morning.prefilter import EmbeddingArticleFilter
from rss_morning.config import TopicCluster


class FakeEmbeddingBackend:
    def __init__(self, responses):
        # response map: text (or tuple of texts) -> list of vectors
        # For simplicity, we assume single text queries for anchors
        self._responses = responses
        self.calls = []

    def embed(self, texts):
        # texts is a list of strings
        self.calls.append(texts)
        results = []
        for text in texts:
            # We allow partial matching or exact key matching
            if text in self._responses:
                results.append(self._responses[text])
            else:
                # fallback or error
                # Try to look for a key that contains this text?
                # tailored for tests
                found = False
                for k, v in self._responses.items():
                    if k == text:
                        results.append(v)
                        found = True
                        break
                if not found:
                    # Return zeros or raise
                    raise AssertionError(f"No fake embedding configured for {text!r}")
        return results


@pytest.fixture
def mock_topics():
    # Use a small set of topics for testing to avoid huge mocks
    return [
        TopicCluster("T1", "Topic A", ["keyword1"]),
        TopicCluster("T2", "Topic B", ["keyword2"]),
    ]


def test_filter_assigns_topics(mock_topics):
    # Prepare backend responses
    # 1. Anchors
    # Topic A query: "Topic A: keyword1"
    # Topic B query: "Topic B: keyword2"

    # 2. Articles
    # Article 1 matches Topic A
    # Article 2 matches Topic B

    anchor_a = [1.0, 0.0]
    anchor_b = [0.0, 1.0]

    # Article embeddings
    # Art1 -> [1.0, 0.0] (perfect match A)
    # Art2 -> [0.0, 1.0] (perfect match B)

    backend = FakeEmbeddingBackend(
        {
            "Topic A: keyword1": anchor_a,
            "Topic B: keyword2": anchor_b,
            "Title A\nSummary A": anchor_a,
            "Title B\nSummary B": anchor_b,
        }
    )

    # Init filter (will build anchors) with TOPICS injected
    filt = EmbeddingArticleFilter(backend=backend, topics=mock_topics)

    articles = [
        {"title": "Title A", "summary": "Summary A", "url": "http://a.com"},
        {"title": "Title B", "summary": "Summary B", "url": "http://b.com"},
    ]

    filtered = filt.filter(articles, cluster_threshold=0.9)

    assert len(filtered) == 2

    a_art = next(a for a in filtered if a["title"] == "Title A")
    assert a_art["category"] == "Topic A"
    assert a_art["prefilter_score"] >= 0.99

    b_art = next(a for a in filtered if a["title"] == "Title B")
    assert b_art["category"] == "Topic B"


def test_filter_threshold(mock_topics):
    anchor_a = [1.0, 0.0]
    anchor_b = [0.0, 1.0]

    # Article totally unrelated: [0.0, 0.0] (or orthogonal [0.7, 0.7] to both if we used higher dims,
    # but here [0,0] is invalid, let's use [0.707, 0.707] which is 45 deg to both.
    # Cosine with [1,0] is 0.707. If threshold is 0.8, it should fail.

    backend = FakeEmbeddingBackend(
        {
            "Topic A: keyword1": anchor_a,
            "Topic B: keyword2": anchor_b,
            "Title Weak\nSummary Weak": [0.707, 0.707],
        }
    )

    filt = EmbeddingArticleFilter(backend=backend, topics=mock_topics)

    articles = [
        {
            "title": "Title Weak",
            "summary": "Summary Weak",
            "url": "http://weak.com",
        },
    ]

    filtered = filt.filter(articles, cluster_threshold=0.8)
    assert len(filtered) == 0

    # If we lower threshold, it should pass
    filtered_loose = filt.filter(articles, cluster_threshold=0.6)
    assert len(filtered_loose) == 1
