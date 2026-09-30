from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.backtest.config import HoldoutConfig
from src.backtest.models import BacktestHoldoutEvaluation, BacktestVariantTrial

STATUS_STARTED = "started"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def variant_fingerprint(parameters: Mapping[str, Any]) -> str:
    canonical = json.dumps(parameters, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BacktestLedger(Protocol):
    def register_variant(self, model_family: str, parameters: Mapping[str, Any], description: str) -> int: ...

    def variant_count(self) -> int: ...

    def start_holdout_call(
        self, model_id: str, config: HoldoutConfig, requested_by: str, reason: str
    ) -> tuple[int, int]: ...

    def finish_holdout_call(self, call_id: int, status: str, summary: Mapping[str, Any] | None) -> None: ...

    def holdout_call_count(self, config_version: str) -> int: ...


@dataclass
class InMemoryLedger:
    variants: dict[tuple[str, str], str] = field(default_factory=dict)
    holdout_calls: list[dict[str, Any]] = field(default_factory=list)

    def register_variant(self, model_family: str, parameters: Mapping[str, Any], description: str) -> int:
        self.variants.setdefault((model_family, variant_fingerprint(parameters)), description)
        return len(self.variants)

    def variant_count(self) -> int:
        return len(self.variants)

    def start_holdout_call(
        self, model_id: str, config: HoldoutConfig, requested_by: str, reason: str
    ) -> tuple[int, int]:
        call_number = self.holdout_call_count(config.version) + 1
        self.holdout_calls.append(
            {
                "model_id": model_id,
                "config_version": config.version,
                "config_sha256": config.sha256,
                "requested_by": requested_by,
                "reason": reason,
                "call_number": call_number,
                "variants_tried": self.variant_count(),
                "status": STATUS_STARTED,
                "summary": None,
            }
        )
        return len(self.holdout_calls) - 1, call_number

    def finish_holdout_call(self, call_id: int, status: str, summary: Mapping[str, Any] | None) -> None:
        self.holdout_calls[call_id]["status"] = status
        self.holdout_calls[call_id]["summary"] = dict(summary) if summary is not None else None

    def holdout_call_count(self, config_version: str) -> int:
        return sum(1 for call in self.holdout_calls if call["config_version"] == config_version)


def _default_session_scope() -> AbstractContextManager[Session]:
    from src.database.connection import get_session

    return get_session()


class DatabaseLedger:
    def __init__(self, session_scope: Callable[[], AbstractContextManager[Session]] | None = None) -> None:
        self._session_scope = session_scope or _default_session_scope

    def register_variant(self, model_family: str, parameters: Mapping[str, Any], description: str) -> int:
        fingerprint = variant_fingerprint(parameters)
        with self._session_scope() as session:
            existing = session.execute(
                select(BacktestVariantTrial.id).where(
                    BacktestVariantTrial.model_family == model_family,
                    BacktestVariantTrial.variant_fingerprint == fingerprint,
                )
            ).first()
            if existing is None:
                session.add(
                    BacktestVariantTrial(
                        model_family=model_family,
                        variant_fingerprint=fingerprint,
                        description=description,
                        created_at=_now(),
                    )
                )
                session.flush()
            return int(session.execute(select(func.count(BacktestVariantTrial.id))).scalar_one())

    def variant_count(self) -> int:
        with self._session_scope() as session:
            return int(session.execute(select(func.count(BacktestVariantTrial.id))).scalar_one())

    def start_holdout_call(
        self, model_id: str, config: HoldoutConfig, requested_by: str, reason: str
    ) -> tuple[int, int]:
        with self._session_scope() as session:
            prior = int(
                session.execute(
                    select(func.count(BacktestHoldoutEvaluation.id)).where(
                        BacktestHoldoutEvaluation.config_version == config.version
                    )
                ).scalar_one()
            )
            variants = int(session.execute(select(func.count(BacktestVariantTrial.id))).scalar_one())
            row = BacktestHoldoutEvaluation(
                model_id=model_id,
                config_version=config.version,
                config_sha256=config.sha256,
                holdout_start=config.holdout_start,
                requested_by=requested_by,
                reason=reason,
                call_number=prior + 1,
                variants_tried=variants,
                status=STATUS_STARTED,
                requested_at=_now(),
            )
            session.add(row)
            session.flush()
            return int(row.id), prior + 1

    def finish_holdout_call(self, call_id: int, status: str, summary: Mapping[str, Any] | None) -> None:
        with self._session_scope() as session:
            row = session.get(BacktestHoldoutEvaluation, call_id)
            if row is None:
                raise LookupError(f"holdout evaluation {call_id} was not recorded")
            row.status = status
            row.summary_json = json.dumps(summary, sort_keys=True, default=str) if summary is not None else None
            row.completed_at = _now()

    def holdout_call_count(self, config_version: str) -> int:
        with self._session_scope() as session:
            return int(
                session.execute(
                    select(func.count(BacktestHoldoutEvaluation.id)).where(
                        BacktestHoldoutEvaluation.config_version == config_version
                    )
                ).scalar_one()
            )
