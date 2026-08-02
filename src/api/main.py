from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.api import auth, market, portfolio, stocks
from src.database.connection import get_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    "https://arthasignal.com",
    "https://www.arthasignal.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app = FastAPI(title="ArthaSignal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(stocks.router)
app.include_router(market.router)
app.include_router(portfolio.router)


@app.get("/health")
def health() -> dict:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        database_status = "ok"
    except Exception:
        logger.exception("Health check database connectivity failed")
        database_status = "unreachable"

    return {"status": "ok", "database": database_status}
