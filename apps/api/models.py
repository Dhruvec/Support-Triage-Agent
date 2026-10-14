"""SQLAlchemy ORM models.

Design notes
------------
* **Multi-tenancy**: every domain table carries ``tenant_id``. Queries must
  always filter by tenant; per-tenant KB and policy live behind the same column.
* **Idempotency**: tickets carry a unique ``idempotency_key`` per tenant so
  webhook retries do not create duplicate work.
* **Embeddings**: ``pgvector`` columns back dedupe (open-ticket similarity) and
  KB hybrid retrieval. Dimensions come from settings.
* **Auditability**: every stage of the pipeline records a :class:`StageResult`
  row (latency, tokens, cost, raw payload) enabling trajectory evals.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agent.config import get_settings
from agent.taxonomy import ActionStatus, Decision, RunStatus

_settings = get_settings()
# JSONB on Postgres, falls back to JSON elsewhere (e.g. SQLite in tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")
EMBED_DIM = _settings.embedding_dim


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)


class KBArticle(Base):
    __tablename__ = "kb_articles"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_kb_tenant_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    slug: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_policy_tenant_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_ticket_idem"),
        Index("ix_ticket_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(40), default="webhook")  # webhook|email|form|csv
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    queue: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Customer linkage (read-only mirror from customer-mcp)
    customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    # Classifier outputs (denormalized for fast dashboard queries)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Dedupe
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Decision output
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_reasons: Mapped[list] = mapped_column(JSONType, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list[TicketMessage]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketMessage.created_at"
    )
    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # customer|agent|internal|system
    body_raw: Mapped[str] = mapped_column(Text)
    body_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_internal_note: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED.value)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="runs")
    stages: Mapped[list[StageResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StageResult.started_at"
    )


class StageResult(Base):
    """One row per pipeline stage execution (trajectory eval + tracing surface)."""

    __tablename__ = "stage_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output: Mapped[dict] = mapped_column(JSONType, default=dict)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[AgentRun] = relationship(back_populates="stages")


class PendingAction(Base):
    """A write action awaiting human approval (refunds, account changes, legal)."""

    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict] = mapped_column(JSONType, default=dict)
    risk_topic: Mapped[str] = mapped_column(String(40), default="none")
    status: Mapped[str] = mapped_column(String(20), default=ActionStatus.PENDING.value)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeadLetterJob(Base):
    """A queue job that exhausted its retries.

    The worker writes one row here before the retry budget expires so no inbound
    ticket is ever silently dropped: an operator can inspect the payload and
    replay it. The raw job payload is stored (not just the ticket id) so the DLQ
    is self-contained even when no ticket row was ever created.
    """

    __tablename__ = "dead_letter_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(120), index=True)
    task: Mapped[str] = mapped_column(String(80), default="process_ticket")
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
