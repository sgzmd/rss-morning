"""Embedding-based article pre-filtering."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Dict,
)

import numpy as np
from openai import OpenAI

from .embeddings import EmbeddingBackend, FastEmbedBackend, OpenAIEmbeddingBackend
from . import db

logger = logging.getLogger(__name__)

Article = Mapping[str, object]
MutableArticle = MutableMapping[str, object]

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TopicCluster:
    id: str
    name: str
    keywords: List[str]
    # We will compute this centroid based on keywords
    anchor_embedding: Optional[np.ndarray] = None


TOPICS = [
    TopicCluster(
        "A",
        "Mobile Ecosystem & Endpoint",
        ["Android malware", "iOS jailbreak", "APK tampering", "sideloading threats"],
    ),
    TopicCluster(
        "B",
        "Identity, Auth & Social Engineering",
        ["MFA bypass", "Passkey adoption", "Credential stuffing", "Deepfake scams"],
    ),
    TopicCluster(
        "C",
        "Fraud, Abuse & Trust Safety",
        ["Payment fraud", "Refund scams", "GPS spoofing", "Loyalty point theft"],
    ),
    TopicCluster(
        "D",
        "Infrastructure, Cloud & Supply Chain",
        [
            "Cloud misconfigurations",
            "CI/CD pipeline leaks",
            "Dependency confusion",
            "AWS breach",
        ],
    ),
    TopicCluster(
        "E",
        "Regional Policy (APAC)",
        ["Korea privacy law", "PIPA KISA", "SIM-swap Korea", "APAC e-commerce fraud"],
    ),
]


@dataclass(frozen=True)
class _EmbeddingConfig:
    """Configuration for embedding lookups."""

    model: str = "intfloat/multilingual-e5-large"
    provider: str = "fastembed"
    batch_size: int = 16
    threshold: float = 0.55
    max_article_length: int = 5000
    max_cluster_size: int = 5


class EmbeddingArticleFilter:
    """Embedding-powered article filter that keeps security-relevant content."""

    CONFIG = _EmbeddingConfig()

    @dataclass
    class _ScoredArticle:
        score: float
        article: MutableArticle
        vector: np.ndarray
        category: str

    def __init__(
        self,
        client: Optional[OpenAI] = None,
        *,
        backend: Optional[EmbeddingBackend] = None,
        # Legacy params kept for compatibility but ignored or used for fallback if needed
        query_embeddings_path: Optional[str] = None,
        queries_file: Optional[str] = None,
        queries: Optional[Dict[str, Sequence[str]]] = None,
        config: Optional[_EmbeddingConfig] = None,
        session_factory=None,
    ):
        self._config = config or self.CONFIG
        self._session_factory = session_factory

        if backend is not None and client is not None:
            logger.info(
                "EmbeddingArticleFilter received both backend and client; backend takes precedence."
            )

        if backend is not None:
            self._backend = backend
        elif self._config.provider == "fastembed":
            self._backend = FastEmbedBackend(
                model_name=self._config.model,
                batch_size=self._config.batch_size,
            )
        else:
            resolved_client = client or OpenAI()
            self._backend = OpenAIEmbeddingBackend(
                client=resolved_client,
                model=self._config.model,
                batch_size=self._config.batch_size,
            )

        # Build topic anchors on init
        self._build_topic_anchors()

    def _build_topic_anchors(self):
        """Creates a 'centroid' embedding for each topic based on its keywords."""
        logger.info("Building topic anchors...")
        for topic in TOPICS:
            if topic.anchor_embedding is not None:
                continue

            # We embed the keywords as a single rich query representation
            query_text = f"{topic.name}: " + ", ".join(topic.keywords)
            # The backend expects a list of strings
            embedding = self._backend.embed([query_text])[0]

            # Normalize
            arr = np.array(embedding, dtype=float)
            norm = float(np.linalg.norm(arr))
            if norm:
                arr = arr / norm
            else:
                arr = np.zeros_like(arr)

            topic.anchor_embedding = arr

    @property
    def queries(self) -> Dict[str, Tuple[str, ...]]:
        # partial backward compatibility
        return {t.name: tuple(t.keywords) for t in TOPICS}

    def filter(
        self,
        articles: Iterable[Article],
        *,
        cluster_threshold: Optional[float] = None,
        rng: Optional[random.Random] = None,
    ) -> List[MutableArticle]:
        """
        Stage 1: Vector Filtering (High Recall)
        Keep news only if it is close to AT LEAST one topic.
        """
        materialized = [dict(article) for article in articles]
        if not materialized:
            logger.info("Embedding pre-filter received no articles.")
            return []

        threshold = (
            cluster_threshold
            if cluster_threshold is not None
            else self._config.threshold
        )
        logger.info(f"Filtering noise (Threshold: {threshold})...")

        article_texts = [self._compose_article_text(item) for item in materialized]
        article_urls = [str(item.get("url")) for item in materialized]

        # Get article embeddings
        raw_vectors = self._embed_texts(article_texts, urls=article_urls)

        # Validate and normalize
        valid_indices = []
        news_matrix_list = []

        for i, vector in enumerate(raw_vectors):
            if vector is None:
                continue
            arr = np.asarray(vector, dtype=float)
            norm = float(np.linalg.norm(arr))
            if norm:
                arr = arr / norm
            else:
                arr = np.zeros_like(arr)

            news_matrix_list.append(arr)
            valid_indices.append(i)

        if not news_matrix_list:
            logger.warning("No valid article embeddings found.")
            return []

        news_matrix = np.vstack(news_matrix_list)  # Shape (N_valid, Embedding_Dim)

        # Stack topic anchors: Shape (N_topics, Embedding_Dim)
        topic_matrix = np.vstack([t.anchor_embedding for t in TOPICS])

        # Cosine Similarity
        # Result Shape: (N_valid, N_topics)
        scores = np.dot(news_matrix, topic_matrix.T)

        # Max score across all topics for each news item
        max_scores = np.max(scores, axis=1)
        best_topic_indices = np.argmax(scores, axis=1)

        # Filter
        retained = []

        for idx, score, topic_idx in zip(valid_indices, max_scores, best_topic_indices):
            logger.debug(
                f"Article {article_urls[idx]} score: {score}, topic: {TOPICS[topic_idx].name}, threshold: {threshold}"
            )

            if score >= threshold:
                article = materialized[idx]
                topic = TOPICS[topic_idx]

                article["prefilter_score"] = float(score)
                article["category"] = topic.name  # Assign the best matching topic name
                # article["topic_id"] = topic.id # Optional
                retained.append(article)

        logger.info(
            f"Dropped {len(materialized) - len(retained)} rows. Keeping {len(retained)} items."
        )
        return retained

    def _embed_texts(
        self, texts: Sequence[str], urls: Optional[Sequence[str]] = None
    ) -> List[List[float]]:
        """Generate normalised embedding vectors for the given texts."""
        if not self._session_factory or not urls:
            return self._backend.embed(texts)

        backend_key = self._config.model
        with self._session_factory() as session:
            cached = db.get_embeddings(session, list(urls), backend_key)

        missing_indices = []
        missing_texts = []
        ordered_vectors: List[Optional[List[float]]] = [None] * len(texts)

        for idx, (text, url) in enumerate(zip(texts, urls)):
            if url in cached:
                try:
                    ordered_vectors[idx] = json.loads(cached[url].decode("utf-8"))
                except Exception:
                    logger.warning("Failed to decode vector for %s, re-embedding", url)
                    missing_indices.append(idx)
                    missing_texts.append(text)
            else:
                missing_indices.append(idx)
                missing_texts.append(text)

        if missing_texts:
            logger.info("Computing embeddings for %d new articles", len(missing_texts))
            new_vectors = self._backend.embed(missing_texts)

            to_upsert = {}
            for i, vector in enumerate(new_vectors):
                original_idx = missing_indices[i]
                ordered_vectors[original_idx] = vector
                url = urls[original_idx]
                to_upsert[url] = json.dumps(vector).encode("utf-8")

            with self._session_factory() as session:
                db.upsert_embeddings(session, to_upsert, backend_key)

        final_vectors = []
        for v in ordered_vectors:
            final_vectors.append(v)  # type: ignore

        return final_vectors  # type: ignore

    def _compose_article_text(self, article: Mapping[str, object]) -> str:
        title = str(article.get("title") or "")
        summary = str(article.get("summary") or "")
        body = str(article.get("text") or "")
        return "\n".join(part for part in (title, summary, body) if part).strip()[
            : self._config.max_article_length
        ]

    # Stubs/Legacy support for CLI if needed, or we can just not export them and fix CLI later.
    # The CLI used `load_queries` and `export_security_query_embeddings`.
    # I'll re-implement `load_queries` as a no-op or compat wrapper if possible,
    # but `prefilter_cli.py` imported it directly from `prefilter`.


def load_queries(queries_path: Optional[str] = None) -> Dict[str, Tuple[str, ...]]:
    """Legacy compatibility: returns queries from TOPICS."""
    return {t.name: tuple(t.keywords) for t in TOPICS}


def export_security_query_embeddings(
    output_path: str,
    *,
    config: Optional[_EmbeddingConfig] = None,
    client: Optional[OpenAI] = None,
    queries_file: Optional[str] = None,  # Ignored
    queries: Optional[Sequence[str]] = None,  # Ignored
) -> Path:
    """Persist embeddings for the security queries to disk.

    Updated to export TOPICS anchors.
    """
    export_config = config or EmbeddingArticleFilter.CONFIG
    # This serves to initialize the backend and populate TOPICS anchors via _build_topic_anchors
    _filter_layer = EmbeddingArticleFilter(
        client=client,
        config=export_config,
    )

    # We export the structure expected by the CLI or consumers?
    # CLI exports: model, threshold, queries (list of strings?), embeddings.
    # The old CLI logic flattened everything to "General" or used file categories.
    # Here we have topics.

    query_list = []
    embeddings = []

    # Flatten? Or just export anchors?
    # The CLI was used to avoid re-embedding.
    # In this new design, we build anchors on init.
    # So we can export them.

    for topic in TOPICS:
        query_list.append(f"{topic.name}: " + ", ".join(topic.keywords))
        embeddings.append(
            topic.anchor_embedding.tolist()
            if topic.anchor_embedding is not None
            else []
        )

    payload = {
        "model": export_config.model,
        "threshold": export_config.threshold,
        "queries": query_list,
        "embeddings": embeddings,
    }

    destination = Path(output_path)
    destination.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Exported %d topic anchor embeddings to %s",
        len(payload["embeddings"]),
        destination,
    )
    return destination
