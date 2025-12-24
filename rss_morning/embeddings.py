"""Embedding backend abstractions used by the pre-filter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Protocol, Sequence

from openai import OpenAI
from fastembed import TextEmbedding
from tqdm import tqdm
import sys
import logging

logger = logging.getLogger(__name__)


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


@dataclass
class FastEmbedBackend:
    """FastEmbed-powered implementation of the embedding backend."""

    model_name: str
    batch_size: int
    _model: TextEmbedding = None

    def __post_init__(self):
        # The model is downloaded automatically if needed
        self._model = TextEmbedding(model_name=self.model_name)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []

        # fastembed returns an iterable of numpy arrays (one per text).
        embeddings_generator = self._model.embed(texts, batch_size=self.batch_size)
        total = len(texts)

        if sys.stderr.isatty():
            # Use tqdm for progress bar in terminal
            return [
                e.tolist()
                for e in tqdm(
                    embeddings_generator, total=total, desc="Embedding", unit="doc"
                )
            ]
        else:
            # Use logging for non-interactive environments
            results = []
            for i, e in enumerate(embeddings_generator):
                results.append(e.tolist())
                # Log usage only periodically to avoid spam
                if total >= 10 and (i + 1) % self.batch_size == 0:
                    logger.info(f"Processed {i + 1}/{total} documents for embedding")
            return results
