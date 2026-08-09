"""Explicitly opt-in candidate-model quality and cost evaluation."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Optional, Tuple, cast

from .summaries import REQUIRED_SUMMARY_FIELDS, SummarySettings, generate_summary

CANDIDATES = {
    "bytedance-seed/seed-2.0-mini": (0.10, 0.40),
    "z-ai/glm-4.7-flash": (0.06, 0.40),
    "openai/gpt-4o-mini": (0.15, 0.60),
}


def load_corpus(path: Path) -> list[dict]:
    """Load and expand the committed synthetic corpus."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    articles = []
    for source in payload["articles"]:
        article = dict(source)
        repeated = article.pop("text_repeat", None)
        if repeated:
            article["text"] = str(repeated["value"]) * int(repeated["count"])
        articles.append(article)
    return articles


def score_result(
    articles: list[dict],
    result: dict,
    metrics: list[dict],
    latency: float,
    prices: tuple[float, float],
) -> dict[str, Any]:
    """Calculate deterministic identity, schema, content, and list-cost metrics."""
    expected = [str(item["url"]) for item in articles]
    summaries = result.get("summaries", []) if isinstance(result, dict) else []
    returned = [str(item.get("url")) for item in summaries if isinstance(item, dict)]
    expected_set, returned_set = set(expected), set(returned)
    complete = sum(
        1
        for item in summaries
        if isinstance(item, dict)
        and isinstance(item.get("summary"), dict)
        and all(
            str(item["summary"].get(field, "")).strip()
            for field in REQUIRED_SUMMARY_FIELDS
        )
    )
    input_tokens = sum(int(item.get("input_tokens", 0)) for item in metrics)
    output_tokens = sum(int(item.get("output_tokens", 0)) for item in metrics)
    hallucinated = len([url for url in returned if url not in expected_set])
    duplicates = len(returned) - len(returned_set)
    return {
        "valid_structured_response_rate": 1.0 if isinstance(result, dict) else 0.0,
        "source_url_precision": len(expected_set & returned_set) / len(returned_set)
        if returned_set
        else 0.0,
        "source_url_recall": len(expected_set & returned_set) / len(expected_set)
        if expected_set
        else 1.0,
        "required_field_completeness": complete / len(expected) if expected else 1.0,
        "duplicate_url_count": duplicates,
        "hallucinated_url_count": hallucinated,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "list_price_usd": (input_tokens * prices[0] + output_tokens * prices[1])
        / 1_000_000,
        "latency_seconds": latency,
        "normalized_content_quality": complete / len(expected) if expected else 1.0,
    }


def main(argv: list[str] | None = None) -> int:
    """Run paid evaluation only after the operator sets an explicit approval flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("tests/fixtures/summary_quality_corpus.json"),
    )
    parser.add_argument("--prompt", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.environ.get("RUN_PAID_LLM_EVAL") != "1":
        parser.error(
            "set RUN_PAID_LLM_EVAL=1 to approve paid candidate-model evaluation"
        )
    articles = load_corpus(args.corpus)
    prompt = args.prompt.read_text(encoding="utf-8")
    report = {}
    for model, prices in CANDIDATES.items():
        metrics: list[dict] = []
        started = time.monotonic()
        _, result = cast(
            Tuple[str, Optional[dict]],
            generate_summary(
                articles,
                prompt,
                return_dict=True,
                settings=SummarySettings(
                    model=model, fallback_models=(), cache_enabled=False
                ),
                metrics_sink=metrics.append,
            ),
        )
        report[model] = score_result(
            articles, result or {}, metrics, time.monotonic() - started, prices
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - module execution
    raise SystemExit(main())
