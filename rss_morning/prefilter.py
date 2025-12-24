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
    CONFIG = _EmbeddingConfig()
    DEFAULT_QUERIES: Dict[str, Tuple[str, ...]] = load_queries()
    _cached_query_embeddings: Dict[Tuple[Tuple[str, ...], str], List[List[float]]] = {}
    _cached_centroids: Dict[Tuple[Tuple[str, ...], ...], Dict[str, np.ndarray]] = {}

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
        queries: Optional[Dict[str, Sequence[str]]] = None,
        config: Optional[_EmbeddingConfig] = None,
        session_factory=None,
    ):
        self._config = config or self.CONFIG
        self._session_factory = session_factory
        # Not fully supported with centroids yet, might need refactor or removal
        self._query_embeddings_override: Optional[List[List[float]]] = None

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

        # Removed query_embeddings_path loading for now as logic changed significantly
        # If we need it back, we need to restructure the cache format.

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
        materialized = [dict(article) for article in articles]
        if not materialized:
            logger.info("Embedding pre-filter received no articles.")
            return []

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

            if not article_vectors:
                logger.warning(
                    "Embedding pre-filter failed to obtain article embeddings; "
                    "returning original %d articles",
                    len(materialized),
                )
                return materialized

            threshold = self._config.threshold
            # We will group scored items by category
            scored_by_category: Dict[
                str, List[EmbeddingArticleFilter._ScoredArticle]
            ] = {}

            for original, vector in zip(materialized, article_vectors):
                best_cat, best_score = self._score_against_centroids(vector, centroids)
                if best_cat is None or best_score < threshold:
                    continue

                original["prefilter_score"] = best_score
                original["category"] = best_cat
                # Optional: keep prefilter_match for debug, showing best category
                original["prefilter_match"] = best_cat

                item = EmbeddingArticleFilter._ScoredArticle(
                    score=best_score, article=original, vector=vector, category=best_cat
                )
                if best_cat not in scored_by_category:
                    scored_by_category[best_cat] = []
                scored_by_category[best_cat].append(item)

            retained = []
            max_size = self._config.max_cluster_size

            for category, items in scored_by_category.items():
                # Sort descending by score
                items.sort(key=lambda x: x.score, reverse=True)

                # Keep top N
                kept_items = items[:max_size]

                # We can compute other_urls if needed, based on the kernel (top item)
                # or just list others in the category?
                # The prompt implies top N per cluster (implied category = cluster now).
                # Logic from previous clustering: "Kernel" + "others".
                # Let's treat the top 1 as kernel for metadata structure if UI needs it,
                # but "kept_items" are all valid articles to return.
                # If we want to maintain the "other_urls" structure for the UI to show grouping:
                # The top article (kernel) gets "other_urls" populated with the rest of the kept N-1 articles.
                # The other kept articles get "other_urls" = [].
                # This matches strict clustering behavior where we show 1 item representing the cluster.

                if kept_items:
                    kernel = kept_items[0]
                    others = kept_items[1:]

                    # Calculate distances for others
                    other_entries = []
                    for other in others:
                        cosine = self._cosine(kernel.vector, other.vector)
                        dist = max(0.0, 1.0 - cosine)
                        other_entries.append(
                            {
                                "url": str(other.article.get("url") or ""),
                                "distance": round(dist, 4),
                            }
                        )
                        # Ensure others have empty other_urls
                        other.article["other_urls"] = []

                    kernel.article["other_urls"] = other_entries

                    # Add all kept items to retained list
                    for item in kept_items:
                        retained.append(item.article)

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
            return materialized

    def _get_category_centroids(self) -> Dict[str, np.ndarray]:
        """Fetch and cache centroids for the security query categories."""
        # Use a tuple of sorted items as a stable key for caching
        queries_key = tuple(sorted((k, tuple(v)) for k, v in self._queries.items()))
        key = (queries_key, self._config.model)

        cached = self.__class__._cached_centroids.get(key)
        if cached is not None:
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
        return centroids

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

    def _score_against_centroids(
        self,
        article_vector: Sequence[float],
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
    def _dot(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(lft * rght for lft, rght in zip(left, right))

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
