"""CLI for precomputing embedding vectors used by the article pre-filter."""

from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from .prefilter import (
    EmbeddingArticleFilter,
    _EmbeddingConfig,
    export_security_query_embeddings,
    load_queries,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and persist query embeddings for the article pre-filter."
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Destination file (JSON) to write query embeddings to.",
    )
    parser.add_argument(
        "--model",
        default=EmbeddingArticleFilter.CONFIG.model,
        help="Embedding model identifier to use.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=EmbeddingArticleFilter.CONFIG.batch_size,
        help="Batch size for calls to the embeddings API.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=EmbeddingArticleFilter.CONFIG.threshold,
        help="Filter threshold to store alongside the embeddings metadata.",
    )
    parser.add_argument(
        "--queries-file",
        help="Path to the queries file to embed (defaults to queries.txt or queries.example.txt).",
    )
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging()

    config = _EmbeddingConfig(
        model=args.model, batch_size=args.batch_size, threshold=args.threshold
    )

    queries = load_queries(args.queries_file)

    try:
        export_security_query_embeddings(args.output, config=config, queries=queries)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to export query embeddings.")
        return 1

    print(f"Wrote {len(queries)} query embeddings to {args.output}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
