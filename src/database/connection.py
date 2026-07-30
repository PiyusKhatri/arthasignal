from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)

DEFAULT_STATEMENT_TIMEOUT_SECONDS = 90

engine = create_engine(settings.database_url, poolclass=NullPool, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _open_session() -> Session:
    session = SessionLocal()
    try:
        session.execute(text(f"SET statement_timeout = '{DEFAULT_STATEMENT_TIMEOUT_SECONDS}s'"))
    except Exception:
        session.close()
        raise
    return session


@contextmanager
def get_session() -> Iterator[Session]:
    session = _open_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Database session rolled back due to error")
        raise
    finally:
        session.close()
