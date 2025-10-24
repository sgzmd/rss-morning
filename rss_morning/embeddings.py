"""Embedding backend abstractions used by the pre-filter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Protocol, Sequence

from openai import OpenAI


def normalise_vector(vector: Sequence[float]) -> List[float]:
    """Return the L2-normalised form of the vector."""
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0:
        return [0.0 for component in vector]
    return [component / norm for component in vector]


class EmbeddingBackend(Protocol):
    """Minimal protocol for embedding providers."""

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Return embeddings for the provided texts."""


@dataclass
class OpenAIEmbeddingBackend:
    """OpenAI-powered implementation of the embedding backend."""

    client: OpenAI
    model: str
    batch_size: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []

        embeddings_api = getattr(self.client, "embeddings", None)
        if embeddings_api is None:
            raise RuntimeError("OpenAI client does not expose embeddings API")

        vectors: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = embeddings_api.create(model=self.model, input=batch)
            for item in response.data:
                vectors.append(normalise_vector(item.embedding))
        return vectors
