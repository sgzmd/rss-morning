"""Embedding-based article pre-filtering."""

from __future__ import annotations

import json
import logging
import math
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
    Any,
)

from openai import OpenAI

logger = logging.getLogger(__name__)

Article = Mapping[str, object]
MutableArticle = MutableMapping[str, object]


SECURITY_QUERIES: Tuple[str, ...] = (
    # Mobile & App Security
    "Android malware, trojans, infostealers targeting consumers",
    "iOS vulnerabilities, jailbreak exploits, sideloading threats",
    "Malicious SDKs or libraries embedded in mobile apps",
    "Credential theft or token hijacking from Android or iOS apps",
    "Reverse engineering, tampering, or repackaging of APKs",
    "Abuse of Android accessibility services or screen readers to steal OTPs",
    "Supply chain compromise via mobile ad networks or analytics SDKs",
    "Mobile app data leakage, insecure local storage or logs",
    "App transport security and certificate pinning bypass",
    "Malicious apps or fake app campaigns in Play Store or App Store",
    "Threats to push notification integrity or FCM hijacking",
    # Authentication, Identity & Account Protection
    "MFA and passkey adoption, FIDO2, WebAuthn",
    "Credential stuffing, password spraying, account takeover",
    "Account takeover via social engineering or credential leaks",
    "OAuth, OpenID Connect, or SSO misconfiguration vulnerabilities",
    "Password strength enforcement and breached password reuse",
    # E-Commerce & Payment Security
    "Payment fraud in e-commerce and online marketplaces",
    "Card-not-present fraud or refund scam automation",
    "Compromised merchant or seller accounts",
    # Logistics, Driver, and Gig-Economy Security
    "Delivery driver app exploits, GPS spoofing, gig economy security",
    # Seller / Supplier Portal Security
    "Supplier or merchant portal breaches and credential leaks",
    # Platform & Infrastructure
    "API authentication or authorization flaws",
    "Data breaches involving consumer or merchant PII",
    # Regional & Policy Context
    "Korean privacy or data protection enforcement cases",
    "European data protection / privacy regulations impacting consumers",
)


@dataclass(frozen=True)
class _EmbeddingConfig:
    """Configuration for embedding lookups."""

    model: str = "text-embedding-3-small"
    batch_size: int = 32
    threshold: float = 0.5


class EmbeddingArticleFilter:
    """Embedding-powered article filter that keeps security-relevant content."""

    CONFIG = _EmbeddingConfig()
    QUERIES: Tuple[str, ...] = SECURITY_QUERIES
    _cached_query_key: Optional[Tuple[Tuple[str, ...], str]] = None
    _cached_query_embeddings: Optional[List[List[float]]] = None

    def __init__(
        self,
        client: Optional[OpenAI] = None,
        *,
        query_embeddings_path: Optional[str] = None,
        config: Optional[_EmbeddingConfig] = None,
    ):
        self._client = client or OpenAI()
        self._config = config or self.CONFIG
        self._query_embeddings_override: Optional[List[List[float]]] = None
        if query_embeddings_path:
            self._query_embeddings_override = self._load_query_embeddings(
                Path(query_embeddings_path)
            )

    def filter(self, articles: Iterable[Article]) -> List[MutableArticle]:
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
            article_vectors = self._embed_texts(article_texts)
            if not article_vectors:
                logger.warning(
                    "Embedding pre-filter failed to obtain article embeddings; "
                    "returning original %d articles",
                    len(materialized),
                )
                return materialized

            retained: List[MutableArticle] = []
            threshold = self._config.threshold
            for original, vector in zip(materialized, article_vectors):
                best_idx, best_score = self._score_against_queries(
                    vector, query_vectors
                )
                if best_idx is None or best_score < threshold:
                    continue

                original["prefilter_score"] = best_score
                original["prefilter_match"] = self.QUERIES[best_idx]
                retained.append(original)

            logger.info(
                "Embedding pre-filter retained %d of %d articles",
                len(retained),
                len(materialized),
            )
            return retained or materialized
        except Exception:  # noqa: BLE001
            logger.exception(
                "Embedding pre-filter encountered an error; returning original articles."
            )
            return materialized

    def _get_query_embeddings(self) -> List[List[float]]:
        """Fetch and cache embeddings for the security queries."""
        if self._query_embeddings_override is not None:
            return self._query_embeddings_override

        key = (self.QUERIES, self._config.model)
        if (
            self.__class__._cached_query_key == key
            and self.__class__._cached_query_embeddings
        ):
            return self.__class__._cached_query_embeddings or []

        embeddings = self._embed_texts(list(key[0]))
        self.__class__._cached_query_embeddings = embeddings
        self.__class__._cached_query_key = key
        return embeddings

    def _embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Generate normalised embedding vectors for the given texts."""
        if not texts:
            return []

        try:
            embeddings_api: Any = self._client.embeddings
        except AttributeError:  # pragma: no cover - defensive
            raise RuntimeError("OpenAI client does not expose embeddings API")

        vectors: List[List[float]] = []
        for start in range(0, len(texts), self._config.batch_size):
            batch = texts[start : start + self._config.batch_size]
            response = embeddings_api.create(model=self._config.model, input=batch)
            for item in response.data:
                vectors.append(self._normalise_vector(item.embedding))
        return vectors

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

        if queries != self.QUERIES:
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
        return "\n".join(part for part in (title, summary, body) if part).strip()

    @staticmethod
    def _normalise_vector(vector: Sequence[float]) -> List[float]:
        norm = math.sqrt(sum(component * component for component in vector))
        if norm == 0:
            return [0.0 for component in vector]
        return [component / norm for component in vector]

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


def export_security_query_embeddings(
    output_path: str,
    *,
    config: Optional[_EmbeddingConfig] = None,
    client: Optional[OpenAI] = None,
) -> Path:
    """Persist embeddings for the security queries to disk."""
    export_config = config or EmbeddingArticleFilter.CONFIG
    filter_layer = EmbeddingArticleFilter(client=client, config=export_config)
    embeddings = filter_layer._embed_texts(list(filter_layer.QUERIES))

    payload = {
        "model": export_config.model,
        "threshold": export_config.threshold,
        "queries": list(filter_layer.QUERIES),
        "embeddings": embeddings,
    }

    destination = Path(output_path)
    destination.write_text(json.dumps(payload, indent=2))
    logger.info(
        "Exported %d query embeddings to %s", len(payload["embeddings"]), destination
    )
    return destination
