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
DEFAULT_QUERIES_FILE = PROJECT_ROOT / "queries.txt"
EXAMPLE_QUERIES_FILE = PROJECT_ROOT / "queries.example.txt"


def _load_queries_from_path(path: Path) -> Tuple[str, ...]:
    if not path.is_file():
        raise FileNotFoundError(path)
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return tuple(lines)


def load_queries(queries_path: Optional[str] = None) -> Tuple[str, ...]:
    """Load security queries from a file, falling back to the example file."""
    if queries_path:
        return _load_queries_from_path(Path(queries_path))

    for candidate in (DEFAULT_QUERIES_FILE, EXAMPLE_QUERIES_FILE):
        try:
            return _load_queries_from_path(candidate)
        except FileNotFoundError:
            continue

    raise RuntimeError(
        "No queries file found. Provide queries.txt or queries.example.txt."
    )


@dataclass(frozen=True)
class _EmbeddingConfig:
    """Configuration for embedding lookups."""

    model: str = "intfloat/multilingual-e5-large"
    provider: str = "fastembed"
    batch_size: int = 16
    threshold: float = 0.5
    max_article_length: int = 5000
    max_cluster_size: int = 5


class EmbeddingArticleFilter:
    """Embedding-powered article filter that keeps security-relevant content."""

    CONFIG = _EmbeddingConfig()
    DEFAULT_QUERIES: Tuple[str, ...] = load_queries()
    _cached_query_embeddings: Dict[Tuple[Tuple[str, ...], str], List[List[float]]] = {}

    @dataclass
    class _ScoredArticle:
        score: float
        article: MutableArticle
        vector: np.ndarray

    def __init__(
        self,
        client: Optional[OpenAI] = None,
        *,
        backend: Optional[EmbeddingBackend] = None,
        query_embeddings_path: Optional[str] = None,
        queries_file: Optional[str] = None,
        queries: Optional[Sequence[str]] = None,
        config: Optional[_EmbeddingConfig] = None,
        session_factory=None,
    ):
        self._config = config or self.CONFIG
        self._session_factory = session_factory
        self._query_embeddings_override: Optional[List[List[float]]] = None
        if backend is not None and client is not None:
            logger.info(
                "EmbeddingArticleFilter received both backend and client; backend takes precedence."
            )

        if queries is not None and queries_file is not None:
            raise ValueError("Provide either queries or queries_file, not both.")

        if queries is not None:
            loaded_queries = tuple(queries)
        elif queries_file is not None:
            loaded_queries = load_queries(queries_file)
        else:
            loaded_queries = self.DEFAULT_QUERIES

        self._queries: Tuple[str, ...] = loaded_queries
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

        if query_embeddings_path:
            self._query_embeddings_override = self._load_query_embeddings(
                Path(query_embeddings_path)
            )

    @property
    def queries(self) -> Tuple[str, ...]:
        return self._queries

    def filter(
        self,
        articles: Iterable[Article],
        *,
        cluster_threshold: Optional[float] = None,
        rng: Optional[random.Random] = None,
    ) -> List[MutableArticle]:
        """Return the list of articles that pass the embedding filter."""
        materialized = [dict(article) for article in articles]
        if not materialized:
            logger.info("Embedding pre-filter received no articles.")
            return []

        try:
            query_vectors = self._get_query_embeddings()
            if not query_vectors:
                logger.warning(
                    "Embedding pre-filter failed to obtain query embeddings."
                )
                return materialized

            article_texts = [self._compose_article_text(item) for item in materialized]
            article_urls = [str(item.get("url")) for item in materialized]
            raw_vectors = self._embed_texts(article_texts, urls=article_urls)
            article_vectors: List[np.ndarray] = []
            for vector in raw_vectors or []:
                arr = np.asarray(vector, dtype=float)
                norm = float(np.linalg.norm(arr))
                if norm:
                    arr = arr / norm
                else:
                    arr = np.zeros_like(arr)
                article_vectors.append(arr)
            if not article_vectors:
                logger.warning(
                    "Embedding pre-filter failed to obtain article embeddings; "
                    "returning original %d articles",
                    len(materialized),
                )
                return materialized

            scored_items: List[EmbeddingArticleFilter._ScoredArticle] = []
            threshold = self._config.threshold
            for original, vector in zip(materialized, article_vectors):
                best_idx, best_score = self._score_against_queries(
                    vector, query_vectors
                )
                if best_idx is None or best_score < threshold:
                    continue

                original["prefilter_score"] = best_score
                original["prefilter_match"] = self._queries[best_idx]
                scored_items.append(
                    EmbeddingArticleFilter._ScoredArticle(
                        score=best_score, article=original, vector=vector
                    )
                )

            if scored_items:
                scored_items.sort(key=lambda item: item.score, reverse=True)
                if cluster_threshold is not None:
                    logger.info(
                        "Clustering %d articles at threshold %.2f",
                        len(scored_items),
                        cluster_threshold,
                    )
                    retained = self._apply_clustering(
                        scored_items, cluster_threshold, rng=rng
                    )
                else:
                    for item in scored_items:
                        item.article["other_urls"] = []
                    retained = [item.article for item in scored_items]
            else:
                retained = []

            logger.info(
                "Embedding pre-filter retained %d of %d articles",
                len(retained),
                len(materialized),
            )
            return retained
        except Exception:  # noqa: BLE001
            logger.exception(
                "Embedding pre-filter encountered an error; returning original articles."
            )
            return materialized

    def _get_query_embeddings(self) -> List[List[float]]:
        """Fetch and cache embeddings for the security queries."""
        if self._query_embeddings_override is not None:
            return self._query_embeddings_override

        key = (self._queries, self._config.model)
        cached = self.__class__._cached_query_embeddings.get(key)
        if cached is not None:
            return cached

        embeddings = self._embed_texts(list(self._queries))
        self.__class__._cached_query_embeddings[key] = embeddings
        return embeddings

    def _embed_texts(
        self, texts: Sequence[str], urls: Optional[Sequence[str]] = None
    ) -> List[List[float]]:
        """Generate normalised embedding vectors for the given texts."""
        if not self._session_factory or not urls:
            return self._backend.embed(texts)

        backend_key = self._config.model
        with self._session_factory() as session:
            cached = db.get_embeddings(session, list(urls), backend_key)

        # Determine which texts need embedding
        missing_indices = []
        missing_texts = []
        ordered_vectors: List[Optional[List[float]]] = [None] * len(texts)

        for idx, (text, url) in enumerate(zip(texts, urls)):
            if url in cached:
                # Vectors in DB are bytes (BLOB)
                # Assuming vectors are stored as bytes. Wait, how do we store them?
                # Usually purely binary or specific format.
                # Let's check db.py. It uses LargeBinary.
                # We need to serialize/deserialize.
                # Let's use json for simplicity in serialization if db.py didn't specify.
                # Re-checking db.py plan: "vector (BLOB)".
                # I should use json.dumps/loads for simplicity or struct.pack for efficiency.
                # Given strictness, let's assume we store them as JSON string encoded to bytes for now
                # or just modify db to use JSON/Text if I can, OR handle serialization here.
                # Let's update `db.py` or handle it here.
                # Handling here: JSON string -> bytes.
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

        # Ensure correct return type (all floats)
        final_vectors = []
        for v in ordered_vectors:
            if v is None:
                # Should not happen unless backend failed and we didn't handle it
                # If backend.embed returns results, v is filled.
                # If backend failed it raises.
                # So v should be filled.
                # If missing_texts was empty, v filled from cache.
                pass
            final_vectors.append(v)  # type: ignore

        return final_vectors  # type: ignore

    def _load_query_embeddings(self, path: Path) -> Optional[List[List[float]]]:
        try:
            payload = json.loads(path.read_text())
        except FileNotFoundError:
            logger.warning(
                "Precomputed embedding file %s not found; queries will be embedded live.",
                path,
            )
            return None
        except json.JSONDecodeError:
            logger.warning(
                "Precomputed embedding file %s is not valid JSON; embedding live.", path
            )
            return None

        queries = tuple(payload.get("queries") or [])
        embeddings = payload.get("embeddings") or []
        model = payload.get("model")
        stored_threshold = payload.get("threshold")

        if queries != self._queries:
            logger.warning(
                "Precomputed embeddings at %s use a different query set; embedding live.",
                path,
            )
            return None

        if model and model != self._config.model:
            logger.warning(
                "Precomputed embeddings at %s use model %s but current model is %s; embedding live.",
                path,
                model,
                self._config.model,
            )
            return None

        if stored_threshold is not None and stored_threshold != self._config.threshold:
            logger.info(
                "Embedding threshold in %s (%s) differs from configured threshold (%s); using configured value.",
                path,
                stored_threshold,
                self._config.threshold,
            )

        logger.info(
            "Loaded %d precomputed query embeddings from %s", len(embeddings), path
        )
        return embeddings

    def _compose_article_text(self, article: Mapping[str, object]) -> str:
        title = str(article.get("title") or "")
        summary = str(article.get("summary") or "")
        body = str(article.get("text") or "")
        return "\n".join(part for part in (title, summary, body) if part).strip()[
            : self._config.max_article_length
        ]

    def _score_against_queries(
        self,
        article_vector: Sequence[float],
        query_vectors: Sequence[Sequence[float]],
    ) -> Tuple[Optional[int], float]:
        """Return the index and score of the best matching query."""
        best_idx: Optional[int] = None
        best_score = float("-inf")
        for idx, query_vector in enumerate(query_vectors):
            score = self._dot(article_vector, query_vector)
            if score > best_score:
                best_idx = idx
                best_score = score
        if best_idx is None:
            return None, float("nan")
        return best_idx, best_score

    @staticmethod
    def _dot(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(lft * rght for lft, rght in zip(left, right))

    def _apply_clustering(
        self,
        items: List[_ScoredArticle],
        threshold: float,
        *,
        rng: Optional[random.Random] = None,
    ) -> List[MutableArticle]:
        working_threshold = threshold
        rng = rng or random.Random()
        remaining = list(range(len(items)))
        kernels: List[EmbeddingArticleFilter._ScoredArticle] = []
        cluster_index = 0

        while remaining:
            seed_position = rng.randrange(len(remaining))
            seed_index = remaining.pop(seed_position)
            cluster_indices = [seed_index]
            seed_vector = items[seed_index].vector

            updated_remaining: List[int] = []
            for index in remaining:
                similarity = self._cosine(seed_vector, items[index].vector)
                if similarity >= working_threshold:
                    cluster_indices.append(index)
                else:
                    updated_remaining.append(index)
            remaining = updated_remaining

            cluster_members = [items[idx] for idx in cluster_indices]
            seed_url = str(items[seed_index].article.get("url") or "")
            logger.debug(
                "Cluster %d seeded with %s (%d members)",
                cluster_index + 1,
                seed_url,
                len(cluster_members),
            )
            centroid = self._cluster_centroid(
                [member.vector for member in cluster_members]
            )
            kernel = self._select_kernel(cluster_members, centroid)
            others = [member for member in cluster_members if member is not kernel]
            max_others = max(0, self._config.max_cluster_size - 1)
            kernel.article["other_urls"] = self._build_other_urls(
                kernel, others, limit=max_others
            )
            kernels.append(kernel)
            cluster_index += 1

        kernels.sort(key=lambda item: item.score, reverse=True)
        logger.info(
            "Clustering produced %d kernels from %d articles",
            len(kernels),
            len(items),
        )
        return [item.article for item in kernels]

    def _cluster_centroid(self, vectors: Sequence[np.ndarray]) -> np.ndarray:
        stack = np.stack(vectors, axis=0)
        centroid = np.mean(stack, axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm:
            return centroid / norm
        return np.zeros_like(centroid)

    def _select_kernel(
        self,
        members: Sequence[_ScoredArticle],
        centroid: np.ndarray,
    ) -> _ScoredArticle:
        best_item = members[0]
        best_cosine = self._cosine(best_item.vector, centroid)

        for candidate in members[1:]:
            cosine = self._cosine(candidate.vector, centroid)
            if cosine > best_cosine + 1e-12:
                best_item = candidate
                best_cosine = cosine
                continue
            if abs(cosine - best_cosine) > 1e-12:
                continue

            if candidate.score > best_item.score + 1e-12:
                best_item = candidate
                best_cosine = cosine
                continue
            if abs(candidate.score - best_item.score) > 1e-12:
                continue

            current_url = str(best_item.article.get("url") or "")
            candidate_url = str(candidate.article.get("url") or "")
            if candidate_url < current_url:
                best_item = candidate
                best_cosine = cosine

        return best_item

    def _build_other_urls(
        self,
        kernel: _ScoredArticle,
        others: Sequence[_ScoredArticle],
        limit: Optional[int] = None,
    ) -> List[Dict[str, object]]:
        if not others:
            return []

        entries = []
        sorted_others = sorted(
            others,
            key=lambda item: 1.0 - self._cosine(kernel.vector, item.vector),
        )
        if limit is not None:
            sorted_others = sorted_others[:limit]

        for item in sorted_others:
            cosine = self._cosine(kernel.vector, item.vector)
            distance = max(0.0, 1.0 - cosine)
            entries.append(
                {
                    "url": str(item.article.get("url") or ""),
                    "distance": round(distance, 4),
                }
            )
        return entries

    @staticmethod
    def _cosine(left: np.ndarray, right: np.ndarray) -> float:
        left_norm = float(np.linalg.norm(left))
        right_norm = float(np.linalg.norm(right))
        if not left_norm or not right_norm:
            return 0.0
        value = float(np.dot(left, right) / (left_norm * right_norm))
        return max(min(value, 1.0), -1.0)


def export_security_query_embeddings(
    output_path: str,
    *,
    config: Optional[_EmbeddingConfig] = None,
    client: Optional[OpenAI] = None,
    queries_file: Optional[str] = None,
    queries: Optional[Sequence[str]] = None,
) -> Path:
    """Persist embeddings for the security queries to disk."""
    export_config = config or EmbeddingArticleFilter.CONFIG
    filter_layer = EmbeddingArticleFilter(
        client=client,
        config=export_config,
        queries_file=queries_file,
        queries=queries,
    )
    query_list = list(filter_layer.queries)
    embeddings = filter_layer._embed_texts(query_list)

    payload = {
        "model": export_config.model,
        "threshold": export_config.threshold,
        "queries": query_list,
        "embeddings": embeddings,
    }

    destination = Path(output_path)
    destination.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Exported %d query embeddings to %s", len(payload["embeddings"]), destination
    )
    return destination
