from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)

STATEMENT_TIMEOUT_SECONDS = 15
CONNECT_TIMEOUT_SECONDS = 10
POOL_SIZE = 5
MAX_OVERFLOW = 2
POOL_TIMEOUT_SECONDS = 30
POOL_RECYCLE_SECONDS = 300

readonly_engine = create_engine(
    settings.database_url_readonly,
    poolclass=QueuePool,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    future=True,
    connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
)

ReadonlySessionLocal = sessionmaker(bind=readonly_engine, autoflush=False, autocommit=False, future=True)


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _open_readonly_session() -> Session:
    session = ReadonlySessionLocal()
    try:
        session.execute(text(f"SET statement_timeout = '{STATEMENT_TIMEOUT_SECONDS}s'"))
    except Exception:
        session.close()
        raise
    return session


@contextmanager
def get_readonly_session() -> Iterator[Session]:
    session = _open_readonly_session()
    try:
        yield session
        session.rollback()
    except Exception:
        session.rollback()
        logger.exception("Readonly database session rolled back due to error")
        raise
    finally:
        session.close()
