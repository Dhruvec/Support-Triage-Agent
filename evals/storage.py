"""Eval results storage (SQLite by default, Postgres optional).

Two tables back the harness:

* :class:`EvalRun` — one row per invocation, capturing the config snapshot so a
  run is reproducible and comparable.
* :class:`EvalResult` — one row per evaluated item with its per-metric scores
  and any judge output, enabling run-to-run diffs and flipped-ticket analysis.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from agent.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class EvalBase(DeclarativeBase):
    pass


class EvalRun(EvalBase):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite: Mapped[str] = mapped_column(String(40))
    dataset_version: Mapped[str] = mapped_column(String(40))
    config_name: Mapped[str] = mapped_column(String(80))
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    judge_rubric_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    num_items: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvalResult(EvalBase):
    __tablename__ = "eval_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(80), index=True)
    slice_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    passed: Mapped[bool] = mapped_column(default=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    expected: Mapped[dict] = mapped_column(JSON, default=dict)
    actual: Mapped[dict] = mapped_column(JSON, default=dict)
    judge_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


_engine: Engine | None = None


def get_eval_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_settings().eval_db_url
        _engine = create_engine(url, future=True)
        EvalBase.metadata.create_all(_engine)
    return _engine


def get_eval_session() -> sessionmaker:
    return sessionmaker(bind=get_eval_engine(), expire_on_commit=False, future=True)


def to_jsonable(obj: object) -> object:
    """Best-effort conversion for storing arbitrary eval payloads."""

    return json.loads(json.dumps(obj, default=str))
