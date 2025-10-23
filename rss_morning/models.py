"""Shared data models for rss_morning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class FeedConfig:
    """Configuration for a single RSS feed."""

    category: str
    title: str
    url: str


@dataclass
class FeedEntry:
    """Simplified RSS feed entry used throughout the app."""

    link: str
    category: str
    title: str
    published: datetime
    summary: Optional[str] = None
