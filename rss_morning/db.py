"""Database abstraction layer for caching articles and embeddings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    LargeBinary,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class ArticleModel(Base):
    """Cached article content."""

    __tablename__ = "articles"

    url = Column(String, primary_key=True)
    title = Column(String, nullable=True)
    content = Column(Text, nullable=True)
    image = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    published = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EmbeddingModel(Base):
    """Cached embeddings for articles."""

    __tablename__ = "embeddings"

    url = Column(String, primary_key=True)
    backend_key = Column(String, primary_key=True)
    vector = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_engine(connection_string: Optional[str]) -> Optional[Engine]:
    """Initialize the database engine."""
    if not connection_string:
        return None

    logger.info("Initializing database connection: %s", connection_string)
    engine = create_engine(connection_string)
    Base.metadata.create_all(engine)
    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory for the given engine."""
    return sessionmaker(bind=engine)


def get_article(session: Session, url: str) -> Optional[dict]:
    """Retrieve an article from the cache."""
    stmt = select(ArticleModel).where(ArticleModel.url == url)
    result = session.execute(stmt).scalar_one_or_none()
    if not result:
        return None

    return {
        "url": result.url,
        "title": result.title,
        "text": result.content,
        "image": result.image,
        "summary": result.summary,
        "published": result.published,
    }


def upsert_article(session: Session, data: dict) -> None:
    """Insert or update an article in the cache."""
    url = data.get("url")
    if not url:
        return

    stmt = select(ArticleModel).where(ArticleModel.url == url)
    existing = session.execute(stmt).scalar_one_or_none()

    published_val = data.get("published")
    if isinstance(published_val, str):
        try:
            published_val = datetime.fromisoformat(published_val)
        except ValueError:
            # If parsing fails, leave as None or keep existing if updating?
            # For now, let's just log or ignore.
            pass

    if existing:
        existing.title = data.get("title")
        existing.content = data.get("text")
        existing.image = data.get("image")
        existing.summary = data.get("summary")
        if published_val:
            existing.published = published_val
        existing.updated_at = datetime.now(timezone.utc)
    else:
        new_article = ArticleModel(
            url=url,
            title=data.get("title"),
            content=data.get("text"),
            image=data.get("image"),
            summary=data.get("summary"),
            published=published_val,
            updated_at=datetime.now(timezone.utc),
        )
        session.add(new_article)

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
            # existing_objs[url].created_at = datetime.now(timezone.utc) # Keep original creation time? Or update? Let's keep original.
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
