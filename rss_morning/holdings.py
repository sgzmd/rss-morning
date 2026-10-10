"""Structured, read-only tenant portfolio holdings context parser and validator."""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HoldingItem:
    """Individual portfolio holding record."""

    asset: str
    ticker: Optional[str] = None
    asset_class: Optional[str] = None
    currency: Optional[str] = None
    weight_pct: Optional[float] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class HoldingsData:
    """Validated tenant holdings snapshot with as_of timestamp."""

    as_of: str
    items: List[HoldingItem]


def load_holdings(file_path: str | Path) -> HoldingsData:
    """Load and strictly validate a tenant holdings file (TOML or JSON).

    Rules:
    - Must exist and be a readable file.
    - Must contain a valid, non-empty 'as_of' date/timestamp string.
    - Must contain a 'holdings' list of structured holding items.
    - Each holding must specify at least an 'asset' description or 'ticker'.
    - Malformed files or missing fields fail visibly with descriptive errors.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Configured holdings file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Configured holdings path is not a file: {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ValueError(f"Failed to read holdings file {path}: {exc}") from exc

    try:
        if path.suffix.lower() == ".json":
            raw_data = json.loads(content)
        else:
            raw_data = tomllib.loads(content)
    except Exception as exc:
        raise ValueError(
            f"Malformed holdings file {path}: failed to parse syntax ({exc})"
        ) from exc

    if not isinstance(raw_data, dict):
        raise ValueError(
            f"Malformed holdings file {path}: top-level structure must be a mapping/table."
        )

    as_of = str(raw_data.get("as_of") or "").strip()
    if not as_of:
        raise ValueError(
            f"Malformed holdings file {path}: missing required non-empty 'as_of' date."
        )

    raw_items = raw_data.get("holdings")
    if raw_items is None or not isinstance(raw_items, list):
        raise ValueError(
            f"Malformed holdings file {path}: missing required 'holdings' list."
        )

    parsed_items: List[HoldingItem] = []
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"Malformed holdings file {path}: holding #{idx} must be an object."
            )

        asset = str(item.get("asset") or "").strip()
        ticker = str(item.get("ticker") or "").strip() or None
        if not asset and not ticker:
            raise ValueError(
                f"Malformed holdings file {path}: holding #{idx} must have an 'asset' or 'ticker'."
            )

        asset_class = str(item.get("asset_class") or "").strip() or None
        currency = str(item.get("currency") or "").strip() or None
        raw_weight = item.get("weight_pct") or item.get("weight")
        weight_pct = float(raw_weight) if raw_weight is not None else None
        notes = str(item.get("notes") or "").strip() or None

        parsed_items.append(
            HoldingItem(
                asset=asset or ticker or "Unnamed Asset",
                ticker=ticker,
                asset_class=asset_class,
                currency=currency,
                weight_pct=weight_pct,
                notes=notes,
            )
        )

    logger.debug(
        "Successfully loaded %d holdings items (as_of=%s) from %s",
        len(parsed_items),
        as_of,
        path,
    )
    return HoldingsData(as_of=as_of, items=parsed_items)


def format_holdings_context(holdings: HoldingsData) -> str:
    """Format holdings into an isolated, data-only context block for prompt assembly.

    Explicit grounding invariants:
    - Holdings are read-only reference data for relevance calibration and conditional implications.
    - Holdings are NOT evidence for market prices, returns, or article claims.
    - Never disclose private account or tenant details.
    """
    lines = [
        f"TENANT PORTFOLIO HOLDINGS CONTEXT (AS OF {holdings.as_of}):",
        "The following positions are authorized, read-only portfolio context:",
        "- Use this context SOLELY to assess relevance and conditional implications for positions held.",
        "- Holdings data is NOT evidence for current prices, yields, returns, or market movements.",
        "- Do NOT claim performance or market changes without supporting source articles.",
        "- Frame portfolio takeaways conditionally (e.g., 'For portfolios holding...').",
        "- Do NOT disclose private client details or account identifiers.",
        "Positions:",
    ]

    for item in holdings.items:
        parts = [f"- {item.asset}"]
        if item.ticker:
            parts.append(f"({item.ticker})")
        if item.asset_class:
            parts.append(f"[{item.asset_class}]")
        if item.currency:
            parts.append(f"Currency: {item.currency}")
        if item.weight_pct is not None:
            parts.append(f"Weight: {item.weight_pct:.1f}%")
        if item.notes:
            parts.append(f"Note: {item.notes}")
        lines.append("  " + " ".join(parts))

    return "\n".join(lines)
