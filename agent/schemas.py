"""Pydantic schemas for every pipeline stage.

Each stage has a single, explicit output schema. This is the backbone of the
project: because every stage is typed, each one can be evaluated in isolation
against the golden set. The LLM is asked to return JSON matching these models
(structured outputs), and the orchestrator validates before proceeding.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agent.taxonomy import (
    Category,
    Decision,
    Language,
    Priority,
    RiskTopic,
    Sentiment,
)


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
class IngestTicket(BaseModel):
    """Normalized inbound ticket before the pipeline runs."""

    tenant_id: str
    source: str = "webhook"
    idempotency_key: str
    subject: str | None = None
    body: str
    customer_id: str | None = None
    external_id: str | None = None
    metadata: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Stage 1 — Normalize (deterministic; no model)
# --------------------------------------------------------------------------- #
class NormalizedTicket(BaseModel):
    subject: str = ""
    body_clean: str
    language: Language = Language.UNKNOWN
    signature_removed: bool = False
    quoted_history_removed: bool = False
    word_count: int = 0


# --------------------------------------------------------------------------- #
# Stage 2 — PII redaction (regex + optional small model)
# --------------------------------------------------------------------------- #
class RedactionResult(BaseModel):
    body_redacted: str
    redactions: dict[str, int] = Field(default_factory=dict)  # entity -> count
    had_pii: bool = False


# --------------------------------------------------------------------------- #
# Stage 3 — Classify (small/fast model)
# --------------------------------------------------------------------------- #
class Classification(BaseModel):
    category: Category
    priority: Priority
    sentiment: Sentiment
    language: Language = Language.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Stage 4 — Dedupe (embeddings, no LLM)
# --------------------------------------------------------------------------- #
class DedupeResult(BaseModel):
    is_duplicate: bool = False
    duplicate_of: str | None = None
    similarity: float = 0.0
    candidate_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage 5 — Retrieve (kb-mcp hybrid search + rerank)
# --------------------------------------------------------------------------- #
class RetrievedArticle(BaseModel):
    article_id: str
    slug: str
    title: str
    score: float
    snippet: str = ""


class RetrievalResult(BaseModel):
    articles: list[RetrievedArticle] = Field(default_factory=list)
    query: str = ""
    bm25_hits: int = 0
    vector_hits: int = 0


# --------------------------------------------------------------------------- #
# Stage 6 — Decide (larger model)
# --------------------------------------------------------------------------- #
class DecisionResult(BaseModel):
    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    risk_topics: list[RiskTopic] = Field(default_factory=list)
    requires_approval: bool = False


# --------------------------------------------------------------------------- #
# Stage 7 — Draft (larger model; grounded with citations)
# --------------------------------------------------------------------------- #
class Citation(BaseModel):
    article_id: str
    quote: str = ""


class Draft(BaseModel):
    body: str
    citations: list[Citation] = Field(default_factory=list)
    internal_note: str = ""


# --------------------------------------------------------------------------- #
# Stage 8 — Guardrails (mixed)
# --------------------------------------------------------------------------- #
class GuardrailCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    score: float | None = None


class GuardrailReport(BaseModel):
    passed: bool = True
    checks: list[GuardrailCheck] = Field(default_factory=list)
    injection_detected: bool = False
    pii_leak_detected: bool = False
    groundedness: float | None = None

    @property
    def failures(self) -> list[GuardrailCheck]:
        return [c for c in self.checks if not c.passed]


# --------------------------------------------------------------------------- #
# Stage 9 — Act (write-back via ticketing-mcp)
# --------------------------------------------------------------------------- #
class PlannedAction(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    requires_approval: bool = False
    risk_topic: RiskTopic = RiskTopic.NONE
    reason: str = ""


class ActResult(BaseModel):
    executed: list[PlannedAction] = Field(default_factory=list)
    pending: list[PlannedAction] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Judge output (used by the eval harness, not the pipeline itself)
# --------------------------------------------------------------------------- #
class JudgeRawScore(BaseModel):
    """Structured output for the rubric judge (LLM-as-judge).

    Each dimension is scored 1–5. Kept here so the judge shares the same
    structured-output plumbing as the pipeline stages.
    """

    groundedness: int = Field(..., ge=1, le=5)
    policy_compliance: int = Field(..., ge=1, le=5)
    completeness: int = Field(..., ge=1, le=5)
    tone: int = Field(..., ge=1, le=5)
    rationale: str = ""


# --------------------------------------------------------------------------- #
# Run envelope
# --------------------------------------------------------------------------- #
class PipelineState(BaseModel):
    """Full state carried through the orchestrator. Serializable for replay."""

    ticket_id: str
    tenant_id: str
    started_at: datetime
    normalized: NormalizedTicket | None = None
    redaction: RedactionResult | None = None
    classification: Classification | None = None
    dedupe: DedupeResult | None = None
    retrieval: RetrievalResult | None = None
    decision: DecisionResult | None = None
    draft: Draft | None = None
    guardrails: GuardrailReport | None = None
    act: ActResult | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    errors: list[str] = Field(default_factory=list)

    @property
    def final_decision(self) -> Decision:
        if self.decision is None:
            return Decision.ESCALATE
        return self.decision.decision
