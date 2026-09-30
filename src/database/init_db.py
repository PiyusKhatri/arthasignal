from __future__ import annotations

import logging

from sqlalchemy import text

from src.backtest.models import BacktestHoldoutEvaluation, BacktestVariantTrial
from src.database.auth_models import RefreshSession  # noqa: F401 - registers table metadata
from src.database.connection import engine
from src.database.e1_models import QuantE1ForwardDecision, QuantE1ForwardRun  # noqa: F401 - registers E1 tables
from src.database.models import Base
from src.database.quant_models import (  # noqa: F401 - registers quant tables
    QuantModelSnapshot,
    QuantRobustnessRun,
    QuantShadowSignal,
    QuantV41ModelSnapshot,
    QuantV41ShadowRun,
    QuantV41ShadowSignal,
)

SCHEMA_UPGRADES = (
    "ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS is_known_holiday BOOLEAN NOT NULL DEFAULT FALSE",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db() -> None:
    logger.info("Creating tables against %s", engine.url.render_as_string(hide_password=True))
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in SCHEMA_UPGRADES:
            connection.execute(text(statement))
    logger.info("Tables created: %s", ", ".join(Base.metadata.tables.keys()))


if __name__ == "__main__":
    init_db()
