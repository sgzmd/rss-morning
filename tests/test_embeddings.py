"""Behavior coverage for embedding provider boundaries."""

from types import SimpleNamespace

import pytest

from rss_morning import embeddings


class Vector:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


def test_normalise_vector_handles_zero_and_nonzero_vectors():
    assert embeddings.normalise_vector([0.0, 0.0]) == [0.0, 0.0]
    assert embeddings.normalise_vector([3.0, 4.0]) == [0.6, 0.8]


def test_openai_backend_empty_missing_api_and_batches():
    backend = embeddings.OpenAIEmbeddingBackend(
        client=SimpleNamespace(), model="model", batch_size=2
    )
    assert backend.embed([]) == []
    with pytest.raises(RuntimeError, match="does not expose"):
        backend.embed(["text"])

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[3.0, 4.0]) for _ in kwargs["input"]]
        )

    backend.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    assert backend.embed(["a", "b", "c"]) == [[0.6, 0.8]] * 3
    assert [call["input"] for call in calls] == [["a", "b"], ["c"]]


def test_fastembed_initialization_empty_and_noninteractive_progress(
    monkeypatch, caplog
):
    model = SimpleNamespace(
        embed=lambda texts, batch_size: (
            Vector([float(i)]) for i, _ in enumerate(texts)
        )
    )
    monkeypatch.setattr(embeddings, "TextEmbedding", lambda model_name: model)
    monkeypatch.setattr(embeddings.sys.stderr, "isatty", lambda: False)
    backend = embeddings.FastEmbedBackend("local-model", batch_size=5)

    assert backend.embed([]) == []
    caplog.set_level("INFO")
    result = backend.embed([str(i) for i in range(10)])
    assert result == [[float(i)] for i in range(10)]
    assert "Processed 5/10" in caplog.text
    assert "Processed 10/10" in caplog.text


def test_fastembed_interactive_progress(monkeypatch):
    model = SimpleNamespace(embed=lambda texts, batch_size: iter([Vector([1.0])]))
    monkeypatch.setattr(embeddings, "TextEmbedding", lambda model_name: model)
    monkeypatch.setattr(embeddings.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(embeddings, "tqdm", lambda values, **_kwargs: values)

    backend = embeddings.FastEmbedBackend("local-model", batch_size=1)

    assert backend.embed(["one"]) == [[1.0]]
