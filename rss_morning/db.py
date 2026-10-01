"""Database abstraction layer for caching articles and embeddings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
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


def init_engine(connection_string: Optional[str]) -> Optional[Engine]:
    """Initialize the database engine."""
    if not connection_string:
        return None

    try:
        from sqlalchemy.engine import make_url

        safe_conn = make_url(connection_string).render_as_string(hide_password=True)
    except Exception:
        safe_conn = "***"
    logger.info("Initializing database connection: %s", safe_conn)
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
