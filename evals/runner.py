"""The eval runner.

Ties together everything the harness needs to answer "did this change make the
agent better or worse?":

1. load a suite (:mod:`evals.suites`) from a versioned dataset
   (:mod:`evals.dataset`),
2. run the real :class:`agent.orchestrator.Orchestrator` per item against the
   offline corpus (:mod:`evals.offline_client`) with an in-memory recorder
   (:mod:`evals.recorder`),
3. score every layer — classification, decision, retrieval, tool use — plus an
   LLM-as-judge pass on drafted replies,
4. persist the run and per-item results for diffing (:mod:`evals.storage`), and
5. evaluate CI gates (escalation recall above all).

Runs are offline by default (stub LLM + heuristic judge), so the same code path
gates every pull request and, with an API key, evaluates real models.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from agent.config import get_settings
from agent.corpus import TENANT_ID, kb_article_id
from agent.orchestrator import Orchestrator, PipelineConfig
from agent.schemas import IngestTicket
from evals.config import EvalConfig, GateThresholds, load_config
from evals.dataset import EvalItem, load_items
from evals.judges.rubric import RubricJudge
from evals.metrics import classification as classification_metrics
from evals.metrics import decision as decision_metrics
from evals.metrics import retrieval as retrieval_metrics
from evals.offline_client import OfflineToolClient
from evals.recorder import EvalRecorder
from evals.suites import get_suite


# --------------------------------------------------------------------------- #
# Result envelopes
# --------------------------------------------------------------------------- #
@dataclass
class ItemResult:
    item_id: str
    slice: str
    passed: bool
    scores: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)
    actual: dict = field(default_factory=dict)
    judge_output: dict | None = None
    latency_ms: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateCheck:
    name: str
    value: float | None
    threshold: float | None
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateResult:
    passed: bool
    checks: list[GateCheck] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "checks": [c.to_dict() for c in self.checks]}


@dataclass
class RunReport:
    run_id: str
    suite: str
    config_name: str
    dataset_version: str
    num_items: int
    metrics: dict
    gates: GateResult
    items: list[ItemResult] = field(default_factory=list)

    def to_dict(self, include_items: bool = True) -> dict:
        d = {
            "run_id": self.run_id,
            "suite": self.suite,
            "config_name": self.config_name,
            "dataset_version": self.dataset_version,
            "num_items": self.num_items,
            "metrics": self.metrics,
            "gates": self.gates.to_dict(),
        }
        if include_items:
            d["items"] = [i.to_dict() for i in self.items]
        return d


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_KNOWN_SLUGS = None


def _normalize_article_id(value: str) -> str:
    """Accept either a KB slug or a pre-hashed id and return the canonical id."""

    global _KNOWN_SLUGS
    if value.startswith("kb-"):
        return value
    if _KNOWN_SLUGS is None:
        from agent.corpus import KB_ARTICLES

        _KNOWN_SLUGS = {a["slug"] for a in KB_ARTICLES}
    if value in _KNOWN_SLUGS:
        return kb_article_id(value)
    return value


def _ticket_for(item: EvalItem) -> IngestTicket:
    return IngestTicket(
        tenant_id=TENANT_ID,
        source="eval",
        idempotency_key=f"eval-{item.id}",
        subject=item.subject,
        body=item.body,
    )


def _gold_escalate(item: EvalItem) -> bool:
    if item.expected.escalate is not None:
        return bool(item.expected.escalate)
    return item.expected.decision == "escalate"


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_suite(
    suite: str = "smoke",
    config: EvalConfig | str = "configs/v1.yaml",
    *,
    dataset_path: str | None = None,
    persist: bool = True,
    judge: RubricJudge | None = None,
) -> RunReport:
    if isinstance(config, str):
        config = load_config(config)

    cfg: EvalConfig = config
    suite_def = get_suite(suite)
    all_items = load_items(path=dataset_path, version=cfg.dataset_version)
    items = suite_def.select(all_items)

    settings = cfg.settings()
    pipeline_cfg: PipelineConfig = cfg.pipeline_config()

    # The offline client is reused across items so dedupe can see prior tickets.
    tools = OfflineToolClient(settings=settings)
    for prior in all_items:
        tools.register_ticket(f"eval-{prior.id}", prior.text)

    judge = judge or RubricJudge(model=cfg.judge_model, force_heuristic=cfg.judge_force_heuristic)

    run_id = uuid.uuid4().hex[:12]
    results: list[ItemResult] = []

    # metric accumulators
    gold_cat: list[str] = []
    pred_cat: list[str] = []
    gold_pri: list[str] = []
    pred_pri: list[str] = []
    gold_sen: list[str] = []
    pred_sen: list[str] = []
    decision_pairs: list[tuple[str, bool, bool, str]] = []
    retrieved: list[list[str]] = []
    relevant: list[set[str]] = []
    judge_pass = 0
    judge_total = 0
    total_cost = 0.0
    total_tokens = 0
    errors = 0

    for item in items:
        recorder = EvalRecorder(run_id=f"{run_id}:{item.id}")
        orch = Orchestrator(
            settings=settings,
            tools=tools,
            recorder=recorder,
            config=pipeline_cfg,
        )
        state = orch.run(_ticket_for(item), item.text)

        pred_decision = state.final_decision.value
        pred_escalate = bool(state.escalated) or pred_decision == "escalate"

        actual = {
            "category": state.classification.category.value if state.classification else None,
            "priority": state.classification.priority.value if state.classification else None,
            "sentiment": state.classification.sentiment.value if state.classification else None,
            "decision": pred_decision,
            "escalate": pred_escalate,
            "escalation_reason": state.escalation_reason,
            "retrieved_ids": (
                [a.article_id for a in state.retrieval.articles] if state.retrieval else []
            ),
            "draft": state.draft.body if state.draft else None,
            "guardrails_passed": state.guardrails.passed if state.guardrails else None,
            "pending_actions": len(state.act.pending) if state.act else 0,
            "executed_actions": len(state.act.executed) if state.act else 0,
        }
        expected = {
            "category": item.expected.category,
            "priority": item.expected.priority,
            "sentiment": item.expected.sentiment,
            "decision": item.expected.decision,
            "escalate": _gold_escalate(item),
            "duplicate_of": item.expected.duplicate_of,
            "article_ids": [_normalize_article_id(a) for a in item.expected.article_ids],
        }

        # --- classification ---
        if item.expected.category and actual["category"]:
            gold_cat.append(item.expected.category)
            pred_cat.append(actual["category"])
        if item.expected.priority and actual["priority"]:
            gold_pri.append(item.expected.priority)
            pred_pri.append(actual["priority"])
        if item.expected.sentiment and actual["sentiment"]:
            gold_sen.append(item.expected.sentiment)
            pred_sen.append(actual["sentiment"])

        # --- decision ---
        decision_pairs.append((item.id, expected["escalate"], pred_escalate, pred_decision))

        # --- retrieval (only when gold labels exist) ---
        if expected["article_ids"]:
            retrieved.append(actual["retrieved_ids"])
            relevant.append(set(expected["article_ids"]))

        # --- judge (only when a reply was drafted) ---
        judge_output = None
        if state.draft is not None:
            kb_snippets = [a.snippet for a in state.retrieval.articles] if state.retrieval else []
            js = judge.score(
                ticket_text=item.text,
                draft_body=state.draft.body,
                kb_snippets=kb_snippets,
                decision=pred_decision,
            )
            judge_output = js.to_dict()
            judge_total += 1
            judge_pass += int(js.passed)

        total_cost += recorder.total_cost
        total_tokens += recorder.total_tokens

        passed_checks = {
            "escalate_ok": pred_escalate == expected["escalate"],
            "decision_ok": (
                item.expected.decision is None or pred_decision == item.expected.decision
            ),
            "category_ok": (
                item.expected.category is None or actual["category"] == item.expected.category
            ),
            "guardrails_ok": state.guardrails.passed if state.guardrails else True,
            "no_fatal_error": state.escalation_reason is None
            or not state.escalation_reason.startswith("pipeline_failure"),
        }
        item_error = recorder.error
        if item_error:
            errors += 1
        results.append(
            ItemResult(
                item_id=item.id,
                slice=item.slice,
                passed=all(passed_checks.values()),
                scores={k: bool(v) for k, v in passed_checks.items()},
                expected=expected,
                actual=actual,
                judge_output=judge_output,
                latency_ms=round(recorder.total_latency_ms, 2),
                tokens=recorder.total_tokens,
                cost_usd=round(recorder.total_cost, 6),
                error=item_error,
            )
        )

    # --- aggregate metrics ---
    cat_m = classification_metrics.compute(gold_cat, pred_cat)
    pri_m = classification_metrics.compute(gold_pri, pred_pri)
    sen_m = classification_metrics.compute(gold_sen, pred_sen)
    dec_m = decision_metrics.compute(decision_pairs)
    ret_m = retrieval_metrics.compute(retrieved, relevant) if retrieved else None

    metrics: dict = {
        "classification": {
            "category": cat_m.to_dict(),
            "priority": pri_m.to_dict(),
            "sentiment": sen_m.to_dict(),
        },
        "decision": dec_m.to_dict(),
        "retrieval": ret_m.to_dict() if ret_m else {"support": 0, "note": "no gold article_ids"},
        "judge": {
            "pass_rate": round(judge_pass / judge_total, 4) if judge_total else None,
            "num_judged": judge_total,
            "mode": judge.model if judge.force_heuristic is False else "heuristic",
        },
        "ops": {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(
                sum(r.latency_ms for r in results) / len(results), 2
            ) if results else 0.0,
            "error_rate": round(errors / len(results), 4) if results else 0.0,
            "pass_rate": round(sum(1 for r in results if r.passed) / len(results), 4)
            if results
            else 0.0,
        },
    }

    gates = evaluate_gates(cfg.gates, metrics)
    report = RunReport(
        run_id=run_id,
        suite=suite,
        config_name=cfg.name,
        dataset_version=cfg.dataset_version,
        num_items=len(items),
        metrics=metrics,
        gates=gates,
        items=results,
    )

    if persist:
        persist_run(report, cfg)

    return report


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def evaluate_gates(gates: GateThresholds, metrics: dict) -> GateResult:
    dec = metrics.get("decision", {})
    cls_acc = metrics.get("classification", {}).get("category", {}).get("accuracy", 0.0)
    judge_rate = metrics.get("judge", {}).get("pass_rate")
    ops = metrics.get("ops", {})

    checks: list[GateCheck] = []

    def add(name: str, value: float | None, threshold: float | None, comparator: str = "min"):
        if threshold is None or value is None:
            checks.append(GateCheck(name, value, threshold, True, "skipped"))
            return
        passed = value >= threshold if comparator == "min" else value <= threshold
        checks.append(GateCheck(name, value, threshold, passed))

    add("escalation_recall", dec.get("escalation_recall"), gates.escalation_recall)
    add("auto_resolve_precision", dec.get("auto_resolve_precision"), gates.auto_resolve_precision)
    add("decision_accuracy", dec.get("accuracy"), gates.decision_accuracy)
    add("classification_accuracy", cls_acc, gates.classification_accuracy)
    add("judge_pass_rate", judge_rate, gates.judge_pass_rate)
    add("max_cost_usd", ops.get("total_cost_usd"), gates.max_cost_usd, comparator="max")
    add("max_error_rate", ops.get("error_rate"), gates.max_error_rate, comparator="max")

    return GateResult(passed=all(c.passed for c in checks), checks=checks)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def persist_run(report: RunReport, cfg: EvalConfig) -> None:
    from evals.storage import EvalResult, EvalRun, get_eval_session, to_jsonable

    settings = get_settings()
    session_factory = get_eval_session()
    with session_factory() as s:
        run = EvalRun(
            id=report.run_id,
            suite=report.suite,
            dataset_version=report.dataset_version,
            config_name=report.config_name,
            config_snapshot=to_jsonable(cfg.snapshot()),
            judge_rubric_version=settings.judge_rubric_version,
            judge_model=cfg.judge_model or "heuristic",
            metrics=to_jsonable(report.metrics),
            num_items=report.num_items,
        )
        s.add(run)
        for r in report.items:
            s.add(
                EvalResult(
                    id=uuid.uuid4().hex,
                    run_id=report.run_id,
                    item_id=r.item_id,
                    slice_name=r.slice,
                    passed=r.passed,
                    scores=to_jsonable(r.scores),
                    expected=to_jsonable(r.expected),
                    actual=to_jsonable(r.actual),
                    judge_output=to_jsonable(r.judge_output) if r.judge_output else None,
                    latency_ms=r.latency_ms,
                    tokens=r.tokens,
                    cost_usd=r.cost_usd,
                    error=r.error,
                )
            )
        s.commit()
