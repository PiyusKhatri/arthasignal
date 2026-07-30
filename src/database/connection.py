from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from src.config import settings

logger = logging.getLogger(__name__)

DEFAULT_STATEMENT_TIMEOUT_SECONDS = 90

engine = create_engine(settings.database_url, poolclass=NullPool, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        session.execute(text(f"SET statement_timeout = '{DEFAULT_STATEMENT_TIMEOUT_SECONDS}s'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Database session rolled back due to error")
        raise
    finally:
        session.close()
