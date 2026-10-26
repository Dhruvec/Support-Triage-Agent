"""FastAPI application: ticket ingest + review API.

Endpoints
---------
* ``POST /health``                    — liveness + DB check
* ``POST /ingest``                    — webhook/email/form ingest (idempotent)
* ``POST /ingest/csv``                — CSV replay for backfills/load tests
* ``GET  /tickets``                   — queue view
* ``GET  /tickets/{id}``              — ticket detail with agent reasoning trace
* ``POST /tickets/{id}/approve``      — approve/reject a pending action
* ``GET  /pending``                   — human approval queue
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import uuid
from typing import Annotated, NamedTuple

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy import select, text

from agent.config import get_settings
from agent.orchestrator import Orchestrator, PipelineConfig
from agent.schemas import IngestTicket
from agent.taxonomy import ActionStatus
from apps.api.db import init_db, session_scope
from apps.api.models import AgentRun, PendingAction, StageResult, Tenant, Ticket, TicketMessage
from apps.api.schemas import (
    ApprovalRequest,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    StageTrace,
    TicketDetail,
    TicketSummary,
)

app = FastAPI(title="Triage Agent API", version="0.1.0")


def _ensure_tenant(session, tenant_id: str) -> None:
    if session.get(Tenant, tenant_id) is None:
        session.add(Tenant(id=tenant_id, name=tenant_id))


def _idempotency_key(req: IngestRequest) -> str:
    if req.idempotency_key:
        return req.idempotency_key
    raw = f"{req.tenant_id}:{req.external_id or ''}:{req.subject or ''}:{req.body}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class _Outcome(NamedTuple):
    """Result of an ingest attempt, shared by the HTTP API and the worker runner."""

    ticket_id: str | None
    response: IngestResponse
    decision: str | None
    escalated: bool
    status: str
    error: str | None
    enqueue: bool


def _summarize(ticket_id: str, state) -> IngestResponse:
    return IngestResponse(
        ticket_id=ticket_id,
        decision=state.decision.decision.value if state.decision else None,
        escalated=state.escalated,
        escalation_reason=state.escalation_reason,
        category=state.classification.category.value if state.classification else None,
        priority=state.classification.priority.value if state.classification else None,
        confidence=state.classification.confidence if state.classification else None,
        duplicate=bool(state.dedupe and state.dedupe.is_duplicate),
    )


def _run_agent(
    *,
    ticket_id: str,
    tenant_id: str,
    source: str,
    subject: str | None,
    body: str,
    customer_id: str | None,
    external_id: str | None,
    metadata: dict | None,
) -> _Outcome:
    """Run the orchestrator for a persisted ticket and write the outcome back."""

    from agent.orchestrator import DBRecorder

    ticket = IngestTicket(
        tenant_id=tenant_id,
        source=source,
        idempotency_key=ticket_id,
        subject=subject,
        body=body,
        customer_id=customer_id,
        external_id=external_id,
        metadata=metadata or {},
    )

    orch = Orchestrator(
        config=PipelineConfig(force_stub_llm=not get_settings().anthropic_api_key),
        recorder=DBRecorder(),
    )
    state = orch.run(ticket, body)

    status = "escalated" if state.escalated else "triaged"
    with session_scope() as s:
        tk = s.get(Ticket, ticket_id)
        if tk:
            tk.status = status
            tk.category = state.classification.category.value if state.classification else None
            tk.priority = state.classification.priority.value if state.classification else None
            tk.sentiment = state.classification.sentiment.value if state.classification else None
            tk.language = state.classification.language.value if state.classification else None
            tk.confidence = state.classification.confidence if state.classification else None
            tk.decision = state.decision.decision.value if state.decision else None
            tk.decision_reasons = state.decision.reasons if state.decision else []
            tk.duplicate_of = state.dedupe.duplicate_of if state.dedupe else None

    response = _summarize(ticket_id, state)
    return _Outcome(ticket_id, response, response.decision, state.escalated, status, None, False)


def _process_ticket(payload: dict, *, run_sync: bool) -> _Outcome:
    """Persist a ticket, then either run the agent inline or mark it for the queue.

    This is the single ingest seam shared by the HTTP API (``run_sync=True``) and
    the arq worker runner (``run_sync=False``). Keeping it in one place means the
    idempotency and state-transition rules are identical on every path.
    """

    req = IngestRequest(**payload)
    if not (req.body or "").strip():
        raise HTTPException(status_code=422, detail="body is required")
    idem = _idempotency_key(req)

    with session_scope() as s:
        _ensure_tenant(s, req.tenant_id)
        existing = s.execute(
            select(Ticket).where(
                Ticket.tenant_id == req.tenant_id, Ticket.idempotency_key == idem
            )
        ).scalar_one_or_none()
        if existing and (not run_sync or existing.status != "processing"):
            # Already handled (or explicitly deferred to the worker): report the
            # stored outcome without re-running the agent.
            escalated = existing.decision == "escalate"
            response = IngestResponse(
                ticket_id=existing.id,
                decision=existing.decision,
                category=existing.category,
                priority=existing.priority,
                confidence=existing.confidence,
                escalated=escalated,
            )
            return _Outcome(
                existing.id, response, existing.decision, escalated, existing.status, None, False
            )
        if existing:
            # A row was persisted earlier but the agent never finished (worker
            # retry / fail-open race). Re-run against the same ticket so the work
            # is never silently dropped.
            ticket_id = existing.id
        else:
            ticket = Ticket(
                tenant_id=req.tenant_id,
                idempotency_key=idem,
                source=req.source,
                external_id=req.external_id,
                subject=req.subject,
                customer_id=req.customer_id,
                status="processing",
                embedding=None,
            )
            s.add(ticket)
            s.flush()
            s.add(TicketMessage(ticket_id=ticket.id, role="customer", body_raw=req.body))
            ticket_id = ticket.id

    if not run_sync:
        # Persisted only; the worker claims it and transitions to triaged/escalated.
        return _Outcome(
            ticket_id, IngestResponse(ticket_id=ticket_id), None, False, "processing", None, True
        )

    return _run_agent(
        ticket_id=ticket_id,
        tenant_id=req.tenant_id,
        source=req.source,
        subject=req.subject,
        body=req.body,
        customer_id=req.customer_id,
        external_id=req.external_id,
        metadata=req.metadata,
    )


def _process(req: IngestRequest) -> IngestResponse:
    """Synchronous ingest: persist the ticket and run the agent before returning."""

    outcome = _process_ticket(req.model_dump(), run_sync=True)
    if outcome.ticket_id is None:
        raise HTTPException(status_code=500, detail=outcome.error or "pipeline failed")
    return outcome.response


@app.on_event("startup")
def _startup() -> None:
    try:
        init_db()
    except Exception:  # noqa: BLE001 - API should still boot to report health
        pass


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = True
    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    return HealthResponse(db=db_ok, version=app.version)


@app.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest, async_mode: bool = Query(False, alias="async")) -> IngestResponse:
    """Ingest a ticket synchronously, or enqueue it when ``?async=true``.

    Async mode requires Redis; if the broker is unreachable the request fails
    open to a synchronous run so an inbound ticket is never dropped.
    """

    if not req.body.strip():
        raise HTTPException(status_code=422, detail="body is required")
    if not async_mode:
        return _process(req)

    outcome = _process_ticket(req.model_dump(), run_sync=False)
    if outcome.ticket_id is None:
        raise HTTPException(status_code=500, detail=outcome.error or "pipeline failed")
    try:
        enqueue_ticket(req.model_dump())
    except Exception:  # noqa: BLE001 - broker down: degrade to inline processing
        return _process(req)
    return outcome.response


@app.post("/ingest/csv")
async def ingest_csv(
    file: Annotated[UploadFile, File()],
    tenant_id: str = Query("default"),
    source: str = Query("csv"),
) -> dict:
    """Replay a CSV of tickets (columns: subject, body, customer_id, external_id)."""

    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    results = []
    for row in reader:
        req = IngestRequest(
            tenant_id=tenant_id,
            source=source,
            subject=row.get("subject"),
            body=row.get("body") or "",
            customer_id=row.get("customer_id"),
            external_id=row.get("external_id"),
            idempotency_key=row.get("external_id"),
        )
        results.append(_process(req))
    return {"processed": len(results), "results": [r.model_dump() for r in results]}


@app.get("/tickets", response_model=list[TicketSummary])
def list_tickets(
    tenant_id: str = Query("default"),
    status: str | None = None,
    queue: str | None = None,
    limit: int = 50,
) -> list[TicketSummary]:
    with session_scope() as s:
        stmt = select(Ticket).where(Ticket.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(Ticket.status == status)
        if queue:
            stmt = stmt.where(Ticket.queue == queue)
        stmt = stmt.order_by(Ticket.created_at.desc()).limit(limit)
        rows = s.execute(stmt).scalars().all()
        return [
            TicketSummary(
                id=t.id, subject=t.subject, status=t.status, queue=t.queue,
                category=t.category, priority=t.priority, sentiment=t.sentiment,
                decision=t.decision, confidence=t.confidence, created_at=t.created_at,
            )
            for t in rows
        ]


@app.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: str) -> TicketDetail:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        runs = s.execute(
            select(AgentRun)
            .where(AgentRun.ticket_id == ticket_id)
            .order_by(AgentRun.created_at.desc())
        ).scalars().all()
        stages: list[StageTrace] = []
        if runs:
            latest = runs[0]
            stage_rows = s.execute(
                select(StageResult)
                .where(StageResult.run_id == latest.id)
                .order_by(StageResult.started_at)
            ).scalars().all()
            stages = [
                StageTrace(
                    stage=r.stage, status=r.status, model=r.model, latency_ms=r.latency_ms,
                    tokens=r.tokens, cost_usd=r.cost_usd, output=r.output, error=r.error,
                )
                for r in stage_rows
            ]
        pending = s.execute(
            select(PendingAction).where(PendingAction.ticket_id == ticket_id)
        ).scalars().all()
        return TicketDetail(
            ticket=TicketSummary(
                id=t.id, subject=t.subject, status=t.status, queue=t.queue,
                category=t.category, priority=t.priority, sentiment=t.sentiment,
                decision=t.decision, confidence=t.confidence, created_at=t.created_at,
            ),
            messages=[
                {"role": m.role, "body": m.body_raw, "created_at": m.created_at.isoformat()}
                for m in t.messages
            ],
            stages=stages,
            pending_actions=[
                {
                    "id": p.id, "tool_name": p.tool_name, "arguments": p.arguments,
                    "risk_topic": p.risk_topic, "status": p.status, "reason": p.reason,
                }
                for p in pending
            ],
        )


@app.get("/pending")
def list_pending(tenant_id: str = Query("default")) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            select(PendingAction).where(
                PendingAction.tenant_id == tenant_id,
                PendingAction.status == ActionStatus.PENDING.value,
            )
        ).scalars().all()
        return [
            {
                "id": p.id, "ticket_id": p.ticket_id, "tool_name": p.tool_name,
                "arguments": p.arguments, "risk_topic": p.risk_topic, "reason": p.reason,
            }
            for p in rows
        ]


@app.post("/tickets/{ticket_id}/approve")
def approve(ticket_id: str, req: ApprovalRequest) -> dict:
    """Approve/reject pending actions for a ticket.

    On approval, the gated write tool is executed. On rejection, it is recorded
    and dropped. This is the only path by which risky writes reach the systems.
    """

    from agent.tools import get_tool_client

    if req.decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=422, detail="decision must be approved|rejected")

    executed = []
    with session_scope() as s:
        rows = s.execute(
            select(PendingAction).where(
                PendingAction.ticket_id == ticket_id,
                PendingAction.status == ActionStatus.PENDING.value,
            )
        ).scalars().all()
        if not rows:
            raise HTTPException(status_code=404, detail="no pending actions")
        tools = get_tool_client()
        for p in rows:
            new_status = (
                ActionStatus.APPROVED.value
                if req.decision == "approved"
                else ActionStatus.REJECTED.value
            )
            p.status = new_status
            p.decided_by = req.decided_by
        actions = [(p.tool_name, dict(p.arguments)) for p in rows]

    if req.decision == "approved":
        for tool_name, args in actions:
            tool = getattr(tools, tool_name, None)
            if tool and not tool_name.startswith("get_"):
                tool(**args)
                executed.append(tool_name)

    return {"ticket_id": ticket_id, "decision": req.decision, "executed": executed}


def enqueue_ticket(payload: dict) -> None:
    """Best-effort async enqueue used by ``/ingest?async=true``."""

    from apps.worker.main import submit_ticket  # local import avoids an import cycle

    asyncio.run(submit_ticket(payload))


@app.post("/internal/worker")
def worker_service(req: dict) -> dict:
    """Runner used by the arq process.

    The worker owns no DB pool: it ships each job here, where the API (which
    holds the pool) performs the persist + agent run. Keeping the worker thin
    and stateless makes retries safe — the run is idempotent per ticket.
    """

    payload = req.get("payload") or req
    run_sync = bool(req.get("run_sync", True))
    outcome = _process_ticket(payload, run_sync=run_sync)
    return {
        "ticket_id": outcome.ticket_id,
        "decision": outcome.decision,
        "escalated": outcome.escalated,
        "status": outcome.status,
        "error": outcome.error,
        "enqueue": outcome.enqueue,
        "response": outcome.response.model_dump(),
    }


def _uuid() -> str:
    return str(uuid.uuid4())
