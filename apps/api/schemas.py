"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    tenant_id: str = "default"
    source: str = "webhook"
    idempotency_key: str | None = None
    subject: str | None = None
    body: str
    customer_id: str | None = None
    external_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class IngestResponse(BaseModel):
    ticket_id: str
    run_id: str | None = None
    decision: str | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    category: str | None = None
    priority: str | None = None
    confidence: float | None = None
    duplicate: bool = False


class TicketSummary(BaseModel):
    id: str
    subject: str | None
    status: str
    queue: str | None
    category: str | None
    priority: str | None
    sentiment: str | None
    decision: str | None
    confidence: float | None
    created_at: datetime


class StageTrace(BaseModel):
    stage: str
    status: str
    model: str | None
    latency_ms: int | None
    tokens: int
    cost_usd: float
    output: dict
    error: str | None


class TicketDetail(BaseModel):
    ticket: TicketSummary
    messages: list[dict]
    stages: list[StageTrace]
    pending_actions: list[dict]


class ApprovalRequest(BaseModel):
    decision: str  # approved | rejected
    decided_by: str
    note: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    db: bool
    version: str
