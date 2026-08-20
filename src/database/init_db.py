from __future__ import annotations

import logging

from src.database.auth_models import RefreshSession  # noqa: F401 - registers table metadata
from src.database.connection import engine
from src.database.models import Base
from src.database.quant_models import QuantShadowSignal  # noqa: F401 - registers quant validation table

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db() -> None:
    logger.info("Creating tables against %s", engine.url.render_as_string(hide_password=True))
    Base.metadata.create_all(engine)
    logger.info("Tables created: %s", ", ".join(Base.metadata.tables.keys()))


if __name__ == "__main__":
    init_db()
