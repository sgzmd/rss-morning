"""Database abstraction layer for caching articles and embeddings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import (
    DateTime,
    LargeBinary,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class ArticleModel(Base):
    """Cached article content."""

    __tablename__ = "articles"

    url: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class EmbeddingModel(Base):
    """Cached embeddings for articles."""

    __tablename__ = "embeddings"

    url: Mapped[str] = mapped_column(String, primary_key=True)
    backend_key: Mapped[str] = mapped_column(String, primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class FeedHttpCacheModel(Base):
    """Last successfully downloaded representation of one configured feed URL."""

    __tablename__ = "feed_http_cache"

    configured_url: Mapped[str] = mapped_column(String, primary_key=True)
    final_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    etag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


def init_engine(connection_string: Optional[str]) -> Optional[Engine]:
    """Initialize the database engine."""
    if not connection_string:
        return None

    # Connection strings commonly contain database credentials.  Keep the
    # operational event visible without copying secrets into logs.
    logger.info("Initializing database connection")
    engine = create_engine(connection_string)
    Base.metadata.create_all(engine)
    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory for the given engine."""
    return sessionmaker(bind=engine)


def get_article(session: Session, url: str) -> Optional[dict]:
    """Retrieve an article from the cache."""
    return get_articles(session, [url]).get(url)


def get_articles(session: Session, urls: List[str]) -> Dict[str, dict]:
    """Retrieve cached articles for all requested URLs in one query."""
    if not urls:
        return {}

    stmt = select(ArticleModel).where(ArticleModel.url.in_(urls))
    results = session.execute(stmt).scalars().all()
    return {
        result.url: {
            "url": result.url,
            "title": result.title,
            "text": result.content,
            "image": result.image,
            "summary": result.summary,
            "published": result.published,
        }
        for result in results
    }


def upsert_article(session: Session, data: dict) -> None:
    """Insert or update an article in the cache."""
    if not data.get("url"):
        return

    upsert_articles(session, [data])


def upsert_articles(session: Session, payloads: List[dict]) -> None:
    """Insert or update multiple cached articles in one transaction."""
    valid_payloads = [data for data in payloads if data.get("url")]
    if not valid_payloads:
        return

    urls = [str(data["url"]) for data in valid_payloads]
    stmt = select(ArticleModel).where(ArticleModel.url.in_(urls))
    existing_by_url = {
        article.url: article for article in session.execute(stmt).scalars().all()
    }

    for data in valid_payloads:
        url = str(data["url"])
        raw_published = data.get("published")
        published_val: Optional[datetime] = None
        if isinstance(raw_published, datetime):
            published_val = raw_published
        elif isinstance(raw_published, str):
            try:
                published_val = datetime.fromisoformat(raw_published)
            except ValueError:
                logger.warning("Ignoring invalid publication date for %s", url)

        existing = existing_by_url.get(url)
        if existing:
            existing.title = data.get("title")
            existing.content = data.get("text")
            existing.image = data.get("image")
            existing.summary = data.get("summary")
            if published_val:
                existing.published = published_val
            existing.updated_at = datetime.now(timezone.utc)
        else:
            session.add(
                ArticleModel(
                    url=url,
                    title=data.get("title"),
                    content=data.get("text"),
                    image=data.get("image"),
                    summary=data.get("summary"),
                    published=published_val,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def get_feed_http_states(session: Session, urls: List[str]) -> Dict[str, dict]:
    """Bulk-load conditional HTTP state by original configured feed URL."""
    if not urls:
        return {}

    stmt = select(FeedHttpCacheModel).where(FeedHttpCacheModel.configured_url.in_(urls))
    results = session.execute(stmt).scalars().all()
    return {
        row.configured_url: {
            "configured_url": row.configured_url,
            "final_url": row.final_url,
            "etag": row.etag,
            "last_modified": row.last_modified,
            "body": row.body,
            "fetched_at": row.fetched_at,
        }
        for row in results
    }


def upsert_feed_http_states(session: Session, states: List[dict]) -> None:
    """Atomically insert or replace successful feed download states."""
    valid_states = [
        state
        for state in states
        if state.get("configured_url") and isinstance(state.get("body"), bytes)
    ]
    if not valid_states:
        return

    urls = [str(state["configured_url"]) for state in valid_states]
    stmt = select(FeedHttpCacheModel).where(FeedHttpCacheModel.configured_url.in_(urls))
    existing_by_url = {
        row.configured_url: row for row in session.execute(stmt).scalars().all()
    }
    for state in valid_states:
        configured_url = str(state["configured_url"])
        final_url = state.get("final_url")
        etag = state.get("etag")
        last_modified = state.get("last_modified")
        body = state["body"]
        fetched_at = state.get("fetched_at") or datetime.now(timezone.utc)
        existing = existing_by_url.get(configured_url)
        if existing:
            existing.final_url = final_url
            existing.etag = etag
            existing.last_modified = last_modified
            existing.body = body
            existing.fetched_at = fetched_at
        else:
            session.add(
                FeedHttpCacheModel(
                    configured_url=configured_url,
                    final_url=final_url,
                    etag=etag,
                    last_modified=last_modified,
                    body=body,
                    fetched_at=fetched_at,
                )
            )

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def get_embeddings(
    session: Session, urls: List[str], backend_key: str
) -> Dict[str, bytes]:
    """Batch retrieve embeddings for a list of URLs and a specific backend."""
    if not urls:
        return {}

    stmt = select(EmbeddingModel).where(
        EmbeddingModel.url.in_(urls),
        EmbeddingModel.backend_key == backend_key,
    )
    results = session.execute(stmt).scalars().all()
    return {row.url: row.vector for row in results}


def upsert_embeddings(
    session: Session, data: Dict[str, bytes], backend_key: str
) -> None:
    """Batch insert embeddings."""
    if not data:
        return

    # For upsert, we can just try to fetch existing ones to update or insert new ones.
    # Since vectors are large blobs, we probably just want to overwrite if exists.
    # Doing it one by one for now or check existence first.

    urls = list(data.keys())
    stmt = select(EmbeddingModel).where(
        EmbeddingModel.url.in_(urls),
        EmbeddingModel.backend_key == backend_key,
    )
    existing_objs = {obj.url: obj for obj in session.execute(stmt).scalars().all()}

    for url, vector in data.items():
        if url in existing_objs:
            existing_objs[url].vector = vector
        else:
            new_embedding = EmbeddingModel(
                url=url,
                backend_key=backend_key,
                vector=vector,
            )
            session.add(new_embedding)

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
