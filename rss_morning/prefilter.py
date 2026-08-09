"""Embedding-based article pre-filtering."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import random
import tempfile
from collections import OrderedDict
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
PREPROCESSING_VERSION = "article-text-v1"
QUERY_EMBEDDING_FORMAT_VERSION = 2
_CENTROID_CACHE_SIZE = 32


class EmbeddingValidationError(ValueError):
    """An embedding backend or cached vector violated the vector contract."""


def _embedding_input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_queries_from_path(path: Path) -> Dict[str, Tuple[str, ...]]:
    if not path.is_file():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Validate dict values are lists of strings
                cleaned = {}
                for k, v in data.items():
                    if isinstance(v, list):
                        cleaned[k] = tuple(str(x).strip() for x in v if str(x).strip())
                return cleaned
            elif isinstance(data, list):
                # Fallback for flat list in JSON? Treat as "General" OR raise.
                # Let's map flat list to "General"
                return {
                    "General": tuple(str(x).strip() for x in data if str(x).strip())
                }
        except json.JSONDecodeError:
            pass  # Fallthrough to text handling or raise? Better to fail if .json extension.
            raise

    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return {"General": tuple(lines)}


def load_queries(queries_path: Optional[str] = None) -> Dict[str, Tuple[str, ...]]:
    """Load security queries from a file, falling back to the example file.

    Returns a dictionary mapping category names to tuples of query strings.
    For flat text files, the category is 'General'.
    """
    if queries_path:
        return _load_queries_from_path(Path(queries_path))

    candidates = [
        PROJECT_ROOT / "configs" / "queries.json",
        DEFAULT_QUERIES_FILE.with_suffix(".json"),
        DEFAULT_QUERIES_FILE,
        EXAMPLE_QUERIES_FILE,
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return _load_queries_from_path(candidate)
        except (FileNotFoundError, json.JSONDecodeError):
            continue

    raise RuntimeError(
        "No queries file found. Provide queries.json, queries.txt or queries.example.txt."
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
    DEFAULT_QUERIES: Dict[str, Tuple[str, ...]] = load_queries()
    _cached_centroids: OrderedDict[object, Dict[str, np.ndarray]] = OrderedDict()

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
        query_embeddings_path: Optional[str] = None,
        queries_file: Optional[str] = None,
        queries: Optional[Mapping[str, Sequence[str]]] = None,
        config: Optional[_EmbeddingConfig] = None,
        session_factory=None,
    ):
        self._config = config or self.CONFIG
        self._session_factory = session_factory
        if backend is not None and client is not None:
            logger.info(
                "EmbeddingArticleFilter received both backend and client; backend takes precedence."
            )

        if queries is not None and queries_file is not None:
            raise ValueError("Provide either queries or queries_file, not both.")

        if queries is not None:
            loaded_queries = {k: tuple(v) for k, v in queries.items()}
        elif queries_file is not None:
            loaded_queries = load_queries(queries_file)
        else:
            loaded_queries = self.DEFAULT_QUERIES

        self._queries: Dict[str, Tuple[str, ...]] = loaded_queries
        self._precomputed_centroids: Optional[Dict[str, np.ndarray]] = None
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
            try:
                self._precomputed_centroids = self._load_query_embeddings(
                    Path(query_embeddings_path)
                )
            except Exception as exc:  # noqa: BLE001 - optional cache boundary
                logger.warning(
                    "Ignoring precomputed query embeddings (%s)", type(exc).__name__
                )

    @property
    def queries(self) -> Dict[str, Tuple[str, ...]]:
        return self._queries

    def filter(
        self,
        articles: Iterable[Article],
        *,
        cluster_threshold: Optional[float] = None,
        rng: Optional[random.Random] = None,
    ) -> List[MutableArticle]:
        """Return the list of articles that pass the embedding filter."""
        if cluster_threshold is not None and not 0 <= cluster_threshold <= 1:
            raise ValueError("cluster_threshold must be between 0 and 1")
        pristine: List[MutableArticle] = [dict(article) for article in articles]
        if not pristine:
            logger.info("Embedding pre-filter received no articles.")
            return []
        materialized: List[MutableArticle] = [dict(article) for article in pristine]

        try:
            centroids = self._get_category_centroids()
            if not centroids:
                logger.warning("Embedding pre-filter failed to obtain query centroids.")
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

            threshold = self._config.threshold
            # We will group scored items by category
            scored_by_category: Dict[
                str, List[EmbeddingArticleFilter._ScoredArticle]
            ] = {}

            for original, article_vector in zip(materialized, article_vectors):
                best_cat, best_score = self._score_against_centroids(
                    article_vector, centroids
                )
                if best_cat is None or best_score < threshold:
                    continue

                original["prefilter_score"] = best_score
                original["category"] = best_cat
                # Optional: keep prefilter_match for debug, showing best category
                original["prefilter_match"] = best_cat

                item = EmbeddingArticleFilter._ScoredArticle(
                    score=best_score,
                    article=original,
                    vector=article_vector,
                    category=best_cat,
                )
                if best_cat not in scored_by_category:
                    scored_by_category[best_cat] = []
                scored_by_category[best_cat].append(item)

            retained: List[MutableArticle] = []
            max_size = self._config.max_cluster_size
            similarity_threshold = (
                cluster_threshold if cluster_threshold is not None else 1.0
            )

            for _category, items in scored_by_category.items():
                # Exact ties prefer newer publication timestamps, then lexical URLs.
                items.sort(key=lambda item: str(item.article.get("url") or ""))
                items.sort(
                    key=lambda item: str(item.article.get("published") or ""),
                    reverse=True,
                )
                items.sort(key=lambda item: item.score, reverse=True)

                representatives: List[EmbeddingArticleFilter._ScoredArticle] = []
                for item in items:
                    nearest = None
                    nearest_similarity = float("-inf")
                    for representative in representatives:
                        similarity = self._cosine(item.vector, representative.vector)
                        if similarity > nearest_similarity:
                            nearest = representative
                            nearest_similarity = similarity

                    if (
                        nearest is not None
                        and nearest_similarity >= similarity_threshold
                    ):
                        duplicates = nearest.article["other_urls"]
                        assert isinstance(duplicates, list)
                        duplicates.append(
                            {
                                "url": str(item.article.get("url") or ""),
                                "distance": round(
                                    max(0.0, 1.0 - nearest_similarity), 4
                                ),
                            }
                        )
                    elif len(representatives) < max_size:
                        item.article["other_urls"] = []
                        representatives.append(item)

                retained.extend(item.article for item in representatives)

            logger.info(
                "Embedding pre-filter retained %d articles across %d categories",
                len(retained),
                len(scored_by_category),
            )
            return retained
        except Exception:  # noqa: BLE001
            logger.exception(
                "Embedding pre-filter encountered an error; returning all articles."
            )
            return pristine

    def _get_category_centroids(self) -> Dict[str, np.ndarray]:
        """Fetch and cache centroids for the security query categories."""
        if self._precomputed_centroids is not None:
            return self._precomputed_centroids
        # Use a tuple of sorted items as a stable key for caching
        queries_key = tuple(sorted((k, tuple(v)) for k, v in self._queries.items()))
        key = (queries_key, self._backend_identity())

        cached = self.__class__._cached_centroids.get(key)
        if cached is not None:
            self.__class__._cached_centroids.move_to_end(key)
            return cached

        centroids = {}
        for category, query_list in self._queries.items():
            if not query_list:
                continue
            embeddings = self._embed_texts(list(query_list))
            # Compute centroid
            stack = np.stack(embeddings, axis=0)
            mean_vec = np.mean(stack, axis=0)
            norm = float(np.linalg.norm(mean_vec))
            if norm:
                mean_vec = mean_vec / norm
            else:
                mean_vec = np.zeros_like(mean_vec)
            centroids[category] = mean_vec

        self.__class__._cached_centroids[key] = centroids
        while len(self.__class__._cached_centroids) > _CENTROID_CACHE_SIZE:
            self.__class__._cached_centroids.popitem(last=False)
        return centroids

    def _backend_identity(self) -> str:
        return ":".join(
            (self._config.provider, self._config.model, PREPROCESSING_VERSION)
        )

    def _load_query_embeddings(self, path: Path) -> Dict[str, np.ndarray]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = (
            payload.get("format_version") == QUERY_EMBEDDING_FORMAT_VERSION
            and payload.get("provider") == self._config.provider
            and payload.get("model") == self._config.model
            and payload.get("preprocessing_version") == PREPROCESSING_VERSION
        )
        if not expected:
            raise EmbeddingValidationError("incompatible query embedding metadata")
        dimension = payload.get("dimension")
        if not isinstance(dimension, int) or dimension <= 0:
            raise EmbeddingValidationError("invalid query embedding dimension")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise EmbeddingValidationError("invalid query embedding entries")
        expected_pairs = {
            (category, query)
            for category, queries in self._queries.items()
            for query in queries
        }
        vectors: Dict[str, List[np.ndarray]] = {}
        seen = set()
        for entry in entries:
            pair = (entry.get("category"), entry.get("query"))
            vector = np.asarray(entry.get("vector"), dtype=float)
            if (
                pair in seen
                or pair not in expected_pairs
                or vector.shape != (dimension,)
                or not np.all(np.isfinite(vector))
            ):
                raise EmbeddingValidationError("invalid query embedding entry")
            seen.add(pair)
            vectors.setdefault(str(pair[0]), []).append(vector)
        if seen != expected_pairs:
            raise EmbeddingValidationError("query embedding file is incomplete")
        centroids = {}
        for category, items in vectors.items():
            mean = np.mean(np.stack(items), axis=0)
            norm = float(np.linalg.norm(mean))
            centroids[category] = mean / norm if norm else np.zeros_like(mean)
        return centroids

    def _embed_backend(
        self, texts: Sequence[str], expected_dimension: Optional[int] = None
    ) -> List[List[float]]:
        vectors = self._backend.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingValidationError(
                f"embedding count {len(vectors)} does not match input count {len(texts)}"
            )
        if not vectors:
            return []
        dimensions = {len(vector) for vector in vectors}
        if 0 in dimensions or len(dimensions) != 1:
            raise EmbeddingValidationError(
                "embedding dimensions must be identical and nonzero"
            )
        dimension = next(iter(dimensions))
        if expected_dimension is not None and dimension != expected_dimension:
            raise EmbeddingValidationError(
                "embedding dimension does not match cached vectors"
            )
        return [[float(value) for value in vector] for vector in vectors]

    def _embed_texts(
        self, texts: Sequence[str], urls: Optional[Sequence[str]] = None
    ) -> List[List[float]]:
        """Generate normalised embedding vectors for the given texts."""
        if not self._session_factory or not urls:
            return self._embed_backend(texts)

        if len(urls) != len(texts):
            raise EmbeddingValidationError(
                "URL count does not match embedding input count"
            )

        backend_identity = self._backend_identity()
        input_hashes = {
            str(url): _embedding_input_hash(text) for text, url in zip(texts, urls)
        }
        with self._session_factory() as session:
            cached = db.get_embeddings_v2(session, input_hashes, backend_identity)

        # Determine which texts need embedding
        missing_indices = []
        missing_texts = []
        ordered_vectors: List[Optional[List[float]]] = [None] * len(texts)

        expected_dimension = None
        for idx, (text, raw_url) in enumerate(zip(texts, urls)):
            url = str(raw_url)
            record = cached.get(url)
            if record:
                try:
                    dimension = int(record["dimension"])
                    raw_vector = bytes(record["vector"])
                    if dimension <= 0 or len(raw_vector) != dimension * 4:
                        raise EmbeddingValidationError("corrupt cached vector")
                    cached_vector = np.frombuffer(raw_vector, dtype=np.float32).astype(
                        float
                    )
                    if (
                        expected_dimension is not None
                        and dimension != expected_dimension
                    ):
                        raise EmbeddingValidationError(
                            "cached vector dimension mismatch"
                        )
                    expected_dimension = dimension
                    ordered_vectors[idx] = cached_vector.tolist()
                except Exception:
                    logger.warning("Failed to decode vector for %s, re-embedding", url)
                    missing_indices.append(idx)
                    missing_texts.append(text)
            else:
                missing_indices.append(idx)
                missing_texts.append(text)

        if missing_texts:
            logger.info("Computing embeddings for %d new articles", len(missing_texts))
            new_vectors = self._embed_backend(missing_texts, expected_dimension)

            to_upsert = []
            for i, vector in enumerate(new_vectors):
                original_idx = missing_indices[i]
                ordered_vectors[original_idx] = vector
                url = str(urls[original_idx])
                compact = np.asarray(vector, dtype=np.float32)
                to_upsert.append(
                    {
                        "url": url,
                        "input_hash": input_hashes[url],
                        "backend_identity": backend_identity,
                        "dimension": len(vector),
                        "vector": compact.tobytes(),
                    }
                )

            with self._session_factory() as session:
                db.upsert_embeddings_v2(session, to_upsert)

        # Ensure correct return type (all floats)
        final_vectors: List[List[float]] = []
        for v in ordered_vectors:
            if v is None:
                raise EmbeddingValidationError("missing embedding vector")
            final_vectors.append(v)

        return final_vectors

    def _compose_article_text(self, article: Mapping[str, object]) -> str:
        title = str(article.get("title") or "")
        summary = str(article.get("summary") or "")
        body = str(article.get("text") or "")
        return "\n".join(part for part in (title, summary, body) if part).strip()[
            : self._config.max_article_length
        ]

    def _score_against_centroids(
        self,
        article_vector: np.ndarray,
        centroids: Dict[str, np.ndarray],
    ) -> Tuple[Optional[str], float]:
        """Return the category and score of the best matching centroid."""
        best_cat: Optional[str] = None
        best_score = float("-inf")

        for category, centroid in centroids.items():
            score = self._dot(article_vector, centroid)
            if score > best_score:
                best_cat = category
                best_score = score

        if best_cat is None:
            return None, float("nan")
        return best_cat, best_score

    @staticmethod
    def _dot(left: np.ndarray, right: np.ndarray) -> float:
        if left.shape != right.shape or left.ndim != 1:
            raise EmbeddingValidationError("embedding dimension mismatch")
        return float(np.dot(left, right))

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
    backend: Optional[EmbeddingBackend] = None,
    queries_file: Optional[str] = None,
    queries: Optional[Mapping[str, Sequence[str]]] = None,
) -> Path:
    """Persist embeddings for the security queries to disk."""
    export_config = config or EmbeddingArticleFilter.CONFIG
    filter_layer = EmbeddingArticleFilter(
        client=client,
        backend=backend,
        config=export_config,
        queries_file=queries_file,
        queries=queries,
    )
    pairs = [
        (category, query)
        for category, query_values in filter_layer.queries.items()
        for query in query_values
    ]
    query_list = [query for _category, query in pairs]
    embeddings = filter_layer._embed_texts(query_list)
    dimension = len(embeddings[0]) if embeddings else 0

    payload: Dict[str, object] = {
        "format_version": QUERY_EMBEDDING_FORMAT_VERSION,
        "provider": export_config.provider,
        "model": export_config.model,
        "preprocessing_version": PREPROCESSING_VERSION,
        "dimension": dimension,
        "entries": [
            {"category": category, "query": query, "vector": vector}
            for (category, query), vector in zip(pairs, embeddings)
        ],
    }

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    logger.info("Exported %d query embeddings to %s", len(embeddings), destination)
    return destination
