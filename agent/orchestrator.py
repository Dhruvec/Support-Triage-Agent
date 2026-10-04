"""Agent orchestrator — an explicit state machine.

The pipeline is deliberately *not* one giant prompt. Each stage runs in order,
is typed, and is recorded. Two invariants are enforced here rather than relied
on from the model:

1. **Low confidence always escalates.** If the classifier or decide stage
   confidence is below the configured threshold, the run escalates.
2. **Any failure escalates.** An exception in any stage (LLM or tool) marks the
   run escalated with a reason; tickets are never silently dropped.

Risk topics (refund, account_change, legal, PII) force human approval: their
write actions are recorded as :class:`PendingAction` rows instead of executed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from agent.config import Settings, get_settings
from agent.embeddings import get_embedder
from agent.llm import LLMClient, get_llm_client
from agent.pii import redact
from agent.schemas import (
    ActResult,
    IngestTicket,
    PipelineState,
    PlannedAction,
)
from agent.stages import (
    classify,
    decide,
    dedupe,
    draft,
    normalize,
    run_guardrails,
    stable_hash,
)
from agent.taxonomy import Decision, RiskTopic
from agent.tools import ToolClient, get_tool_client

ESCALATION_RISK_TOPICS = {
    RiskTopic.REFUND,
    RiskTopic.ACCOUNT_CHANGE,
    RiskTopic.LEGAL,
}


# --------------------------------------------------------------------------- #
# Run recording (decoupled from the DB so evals can run without Postgres)
# --------------------------------------------------------------------------- #
class RunRecorder(Protocol):
    def start_run(self, state: PipelineState, config: dict) -> str: ...
    def record_stage(
        self, run_id: str, stage: str, output: dict, *, model: str | None,
        tokens: int, cost: float, latency_ms: float, error: str | None, input_hash: str
    ) -> None: ...
    def finish_run(self, run_id: str, state: PipelineState, error: str | None) -> None: ...
    def record_pending(self, state: PipelineState, action: PlannedAction) -> None: ...


class NullRecorder:
    """No-op recorder used by the eval harness."""

    def start_run(self, state: PipelineState, config: dict) -> str:
        return "null-run"

    def record_stage(self, run_id: str, stage: str, output: dict, **_: object) -> None:
        return None

    def finish_run(self, run_id: str, state: PipelineState, error: str | None) -> None:
        return None

    def record_pending(self, state: PipelineState, action: PlannedAction) -> None:
        return None


class DBRecorder:
    """Persists AgentRun / StageResult / PendingAction rows."""

    def start_run(self, state: PipelineState, config: dict) -> str:
        from apps.api.db import session_scope
        from apps.api.models import AgentRun

        with session_scope() as s:
            run = AgentRun(
                tenant_id=state.tenant_id,
                ticket_id=state.ticket_id,
                status="running",
                config=config,
            )
            s.add(run)
            s.flush()
            return run.id

    def record_stage(
        self, run_id: str, stage: str, output: dict, *, model: str | None = None,
        tokens: int = 0, cost: float = 0.0, latency_ms: float = 0.0,
        error: str | None = None, input_hash: str = ""
    ) -> None:
        from apps.api.db import session_scope
        from apps.api.models import StageResult

        with session_scope() as s:
            s.add(
                StageResult(
                    run_id=run_id,
                    stage=stage,
                    status="error" if error else "ok",
                    model=model,
                    input_hash=input_hash,
                    output=output,
                    tokens=tokens,
                    cost_usd=cost,
                    latency_ms=int(latency_ms),
                    error=error,
                )
            )

    def finish_run(self, run_id: str, state: PipelineState, error: str | None) -> None:
        from apps.api.db import session_scope
        from apps.api.models import AgentRun

        with session_scope() as s:
            run = s.get(AgentRun, run_id)
            if run:
                stages = run.stages
                run.status = "failed" if error else "succeeded"
                run.decision = state.decision.decision.value if state.decision else None
                run.error = error
                run.total_cost_usd = sum(st.cost_usd for st in stages)
                run.total_tokens = sum(st.tokens for st in stages)
                run.latency_ms = sum(st.latency_ms or 0 for st in stages)
                run.finished_at = datetime.now(timezone.utc)

    def record_pending(self, state: PipelineState, action: PlannedAction) -> None:
        from apps.api.db import session_scope
        from apps.api.models import PendingAction

        with session_scope() as s:
            s.add(
                PendingAction(
                    tenant_id=state.tenant_id,
                    ticket_id=state.ticket_id,
                    tool_name=action.tool_name,
                    arguments=action.arguments,
                    risk_topic=action.risk_topic.value,
                    reason=action.reason,
                )
            )


@dataclass
class PipelineConfig:
    prompt_version: str = "v1"
    retrieval_k: int = 5
    dedupe_threshold: float = 0.92
    force_stub_llm: bool = False

    def snapshot(self) -> dict:
        return {
            "prompt_version": self.prompt_version,
            "retrieval_k": self.retrieval_k,
            "dedupe_threshold": self.dedupe_threshold,
            "force_stub_llm": self.force_stub_llm,
        }


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        tools: ToolClient | None = None,
        recorder: RunRecorder | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.config = config or PipelineConfig()
        self.llm = llm or get_llm_client(self.settings, force_stub=self.config.force_stub_llm)
        self.tools = tools or get_tool_client(self.settings)
        self.recorder = recorder or NullRecorder()
        self._embedder = get_embedder()

    # -- helpers ----------------------------------------------------------- #
    def _stage_meta(self, model: str | None, completion: object | None) -> dict:
        if completion is None:
            return {"model": model, "tokens": 0, "cost": 0.0}
        return {
            "model": getattr(completion, "model", model),
            "tokens": getattr(completion, "tokens", 0),
            "cost": getattr(completion, "cost_usd", 0.0),
        }

    def _escalate(self, state: PipelineState, reason: str) -> None:
        state.escalated = True
        state.escalation_reason = reason
        state.errors.append(reason)

    # -- main -------------------------------------------------------------- #
    def run(self, ticket: IngestTicket, ticket_text: str) -> PipelineState:
        state = PipelineState(
            ticket_id=ticket.idempotency_key,
            tenant_id=ticket.tenant_id,
            started_at=datetime.now(timezone.utc),
        )
        run_id = self.recorder.start_run(state, self.config.snapshot())
        fatal_error: str | None = None

        try:
            self._run_stages(ticket, ticket_text, state, run_id)
        except Exception as exc:  # noqa: BLE001 - fail closed to human
            fatal_error = f"{type(exc).__name__}: {exc}"
            self._escalate(state, f"pipeline_failure: {fatal_error}")
        finally:
            self.recorder.finish_run(run_id, state, fatal_error)

        return state

    def _run_stages(
        self, ticket: IngestTicket, ticket_text: str, state: PipelineState, run_id: str
    ) -> None:
        settings = self.settings

        # --- Stage 1: normalize (deterministic) ---
        t = time.perf_counter()
        normalized = normalize(ticket.subject, ticket_text)
        state.normalized = normalized
        self.recorder.record_stage(
            run_id, "normalize", normalized.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(ticket_text),
        )

        # --- Stage 2: PII redaction ---
        t = time.perf_counter()
        redaction = redact(normalized.body_clean)
        state.redaction = redaction
        self.recorder.record_stage(
            run_id, "redaction", redaction.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(normalized.body_clean),
        )
        body = redaction.body_redacted

        # --- Stage 3: classify (model) ---
        t = time.perf_counter()
        classification, comp = classify(normalized, body, self.llm, settings)
        state.classification = classification
        self.recorder.record_stage(
            run_id, "classify", classification.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(body),
            **self._stage_meta(settings.model_classify, comp),  # type: ignore[arg-type]
        )

        # --- Stage 4: dedupe (embeddings) ---
        t = time.perf_counter()
        embedding = self._embedder.embed(body)
        try:
            similar = self.tools.list_similar_tickets(ticket.tenant_id, embedding, k=5)
            candidates = [(s["ticket_id"], embedding) for s in similar]
        except Exception:  # noqa: BLE001 - dedupe is best-effort
            candidates = []
        dedupe_result = dedupe(embedding, candidates, self.config.dedupe_threshold)
        state.dedupe = dedupe_result
        self.recorder.record_stage(
            run_id, "dedupe", dedupe_result.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(body),
        )

        # --- Stage 5: retrieve (kb-mcp hybrid) ---
        t = time.perf_counter()
        try:
            retrieval = self.tools.search_articles(ticket.tenant_id, body, k=self.config.retrieval_k)
        except Exception as exc:  # noqa: BLE001
            self.recorder.record_stage(
                run_id, "retrieve", {}, latency_ms=(time.perf_counter() - t) * 1000,
                input_hash=stable_hash(body), error=str(exc),
            )
            self._escalate(state, f"retrieval_failure: {exc}")
            return
        state.retrieval = retrieval
        self.recorder.record_stage(
            run_id, "retrieve", retrieval.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(body),
        )

        # --- Confidence gate (rule: low confidence => escalate) ---
        if classification.confidence < settings.confidence_escalate_threshold:
            self._escalate(
                state,
                f"low_classification_confidence:{classification.confidence}",
            )

        # --- Stage 6: decide (model) ---
        t = time.perf_counter()
        decision_result, comp = decide(
            normalized, body, classification, retrieval, self.llm, settings
        )
        state.decision = decision_result
        self.recorder.record_stage(
            run_id, "decide", decision_result.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(body),
            **self._stage_meta(settings.model_decide, comp),  # type: ignore[arg-type]
        )

        if decision_result.confidence < settings.confidence_escalate_threshold:
            self._escalate(state, f"low_decision_confidence:{decision_result.confidence}")

        # --- Stage 7: draft (only when a reply is warranted) ---
        drafted = None
        if decision_result.decision in (Decision.DRAFT, Decision.AUTO_RESOLVE):
            t = time.perf_counter()
            drafted, comp = draft(
                normalized, body, decision_result, retrieval, self.llm, settings
            )
            state.draft = drafted
            self.recorder.record_stage(
                run_id, "draft", drafted.model_dump(mode="json"),
                latency_ms=(time.perf_counter() - t) * 1000,
                input_hash=stable_hash(body),
                **self._stage_meta(settings.model_draft, comp),  # type: ignore[arg-type]
            )

        # --- Stage 8: guardrails ---
        t = time.perf_counter()
        retrieved_ids = {a.article_id for a in retrieval.articles}
        report = run_guardrails(
            ticket_text=ticket_text,
            decision_result=decision_result,
            drafted=drafted,
            retrieved_ids=retrieved_ids,
        )
        state.guardrails = report
        self.recorder.record_stage(
            run_id, "guardrails", report.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(body),
        )
        if not report.passed:
            self._escalate(
                state,
                "guardrail_failure:" + ",".join(c.name for c in report.failures),
            )

        # --- Stage 9: act (write-back, approval-gated) ---
        t = time.perf_counter()
        act = self._plan_actions(state, decision_result, drafted)
        state.act = act
        self.recorder.record_stage(
            run_id, "act", act.model_dump(mode="json"),
            latency_ms=(time.perf_counter() - t) * 1000,
            input_hash=stable_hash(body),
        )

    def _plan_actions(self, state: PipelineState, decision_result, drafted) -> ActResult:
        """Turn the decision into concrete actions, gating risky ones.

        Auto-resolve writes are executed; everything risky is *never* executed
        here — it is recorded as a pending action for a human to approve.
        If the run has escalated, no writes happen at all.
        """

        executed: list[PlannedAction] = []
        pending: list[PlannedAction] = []

        risky = {t for t in decision_result.risk_topics if t != RiskTopic.NONE}

        if state.escalated or risky:
            queue = "escalations"
            if RiskTopic.LEGAL in risky:
                queue = "legal"
            elif RiskTopic.REFUND in risky:
                queue = "refunds"
            pending.append(
                PlannedAction(
                    tool_name="assign_to_queue",
                    arguments={"ticket_id": state.ticket_id, "queue": queue},
                    requires_approval=True,
                    risk_topic=next(iter(risky), RiskTopic.NONE),
                    reason=state.escalation_reason or "risk topic",
                )
            )
            if drafted is not None and drafted.internal_note:
                pending.append(
                    PlannedAction(
                        tool_name="add_internal_note",
                        arguments={"ticket_id": state.ticket_id, "note": drafted.internal_note},
                        requires_approval=True,
                        risk_topic=next(iter(risky), RiskTopic.NONE),
                        reason="draft note pending approval",
                    )
                )
        elif decision_result.decision == Decision.AUTO_RESOLVE:
            executed.append(
                PlannedAction(
                    tool_name="update_ticket",
                    arguments={"ticket_id": state.ticket_id, "status": "resolved"},
                )
            )

        for action in pending:
            self.recorder.record_pending(state, action)

        # Execute only the safe, pre-approved actions.
        for action in executed:
            tool = getattr(self.tools, action.tool_name, None)
            if tool is None:
                continue
            tool(**action.arguments)

        return ActResult(executed=executed, pending=pending)


def run_ticket(
    ticket: IngestTicket,
    *,
    ticket_text: str | None = None,
    config: PipelineConfig | None = None,
    recorder: RunRecorder | None = None,
    force_stub: bool = False,
) -> PipelineState:
    """Convenience entry point used by the API, worker, and eval harness."""

    settings = get_settings()
    cfg = config or PipelineConfig(force_stub_llm=force_stub)
    orch = Orchestrator(
        settings=settings,
        llm=get_llm_client(settings, force_stub=cfg.force_stub_llm or force_stub),
        tools=get_tool_client(settings),
        recorder=recorder or NullRecorder(),
        config=cfg,
    )
    return orch.run(ticket, ticket_text or ticket.body)
