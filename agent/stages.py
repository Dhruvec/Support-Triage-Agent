"""Pipeline stages.

Each stage is a small, independently testable unit. Deterministic stages
(normalize, dedupe) have no model dependency; model stages call the LLM client
and return a validated Pydantic object. Guardrails combine deterministic checks
with optional model-based groundedness.
"""

from __future__ import annotations

import hashlib
import re

from agent.config import Settings, get_settings
from agent.llm import LLMClient
from agent.pii import contains_pii
from agent.prompts import (
    CLASSIFY_SYSTEM,
    DECIDE_SYSTEM,
    DRAFT_SYSTEM,
    classify_user,
    decide_user,
    draft_user,
)
from agent.schemas import (
    Classification,
    DecisionResult,
    Draft,
    DedupeResult,
    GuardrailCheck,
    GuardrailReport,
    NormalizedTicket,
    RetrievalResult,
)
from agent.taxonomy import Decision, Language

# --------------------------------------------------------------------------- #
# Stage 1 — Normalize (deterministic)
# --------------------------------------------------------------------------- #
_QUOTE_MARKERS = ("\n>", "\nOn ", "\n-----Original Message-----", "\nFrom: ")
_SIGNATURE_MARKERS = ("\n-- \n", "\nRegards,", "\nBest,", "\nThanks,\n", "\nSent from my")


def normalize(subject: str | None, body: str) -> NormalizedTicket:
    text = body or ""

    quoted_removed = False
    for marker in _QUOTE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
            quoted_removed = True

    signature_removed = False
    for marker in _SIGNATURE_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
            signature_removed = True

    text = re.sub(r"\s+", " ", text).strip()
    return NormalizedTicket(
        subject=(subject or "").strip(),
        body_clean=text,
        language=_detect_language(text),
        signature_removed=signature_removed,
        quoted_history_removed=quoted_removed,
        word_count=len(text.split()),
    )


_LANG_MARKERS = {
    Language.ES: ("gracias", "no puedo", "ayuda", "factura", "reembolso"),
    Language.FR: ("bonjour", "merci", "problème", "remboursement", "facture"),
    Language.DE: ("hallo", "danke", "problem", "rechnung", "rückerstattung"),
    Language.PT: ("obrigado", "problema", "fatura", "reembolso"),
    Language.HI: ("धन्यवाद", "समस्या", "मदद"),
}


def _detect_language(text: str) -> Language:
    low = text.lower()
    for lang, markers in _LANG_MARKERS.items():
        if any(m in low for m in markers):
            return lang
    return Language.EN if text.strip() else Language.UNKNOWN


# --------------------------------------------------------------------------- #
# Stage 2 — PII redaction
# --------------------------------------------------------------------------- #
# Delegated to agent.pii.redact in the orchestrator (kept as a thin re-export).


# --------------------------------------------------------------------------- #
# Stage 3 — Classify (model)
# --------------------------------------------------------------------------- #
def classify(
    normalized: NormalizedTicket,
    body_redacted: str,
    llm: LLMClient,
    settings: Settings | None = None,
) -> tuple[Classification, object]:
    settings = settings or get_settings()
    completion = llm.complete_structured(
        system=CLASSIFY_SYSTEM,
        user=classify_user(subject=normalized.subject, body=body_redacted),
        schema=Classification,
        model=settings.model_classify,
    )
    return completion.parsed, completion  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Stage 4 — Dedupe (embeddings / similarity)
# --------------------------------------------------------------------------- #
def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def dedupe(
    embedding: list[float] | None,
    candidates: list[tuple[str, list[float]]],
    threshold: float = 0.92,
) -> DedupeResult:
    if not embedding or not candidates:
        return DedupeResult()
    scored = [(cid, cosine(embedding, emb)) for cid, emb in candidates]
    scored.sort(key=lambda t: t[1], reverse=True)
    top_id, top_score = scored[0]
    return DedupeResult(
        is_duplicate=top_score >= threshold,
        duplicate_of=top_id if top_score >= threshold else None,
        similarity=round(top_score, 4),
        candidate_ids=[cid for cid, s in scored[:5] if s >= 0.5],
    )


# --------------------------------------------------------------------------- #
# Stage 6 — Decide (model)
# --------------------------------------------------------------------------- #
def decide(
    normalized: NormalizedTicket,
    body_redacted: str,
    classification: Classification,
    retrieval: RetrievalResult,
    llm: LLMClient,
    settings: Settings | None = None,
    plan: str = "unknown",
    sla: str = "standard",
) -> tuple[DecisionResult, object]:
    settings = settings or get_settings()
    completion = llm.complete_structured(
        system=DECIDE_SYSTEM,
        user=decide_user(
            subject=normalized.subject,
            body=body_redacted,
            category=classification.category.value,
            priority=classification.priority.value,
            sentiment=classification.sentiment.value,
            confidence=classification.confidence,
            kb_titles=[a.title for a in retrieval.articles],
            plan=plan,
            sla=sla,
        ),
        schema=DecisionResult,
        model=settings.model_decide,
    )
    return completion.parsed, completion  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Stage 7 — Draft (model)
# --------------------------------------------------------------------------- #
def draft(
    normalized: NormalizedTicket,
    body_redacted: str,
    decision_result: DecisionResult,
    retrieval: RetrievalResult,
    llm: LLMClient,
    settings: Settings | None = None,
    plan: str = "unknown",
) -> tuple[Draft, object]:
    settings = settings or get_settings()
    articles = [
        {"article_id": a.article_id, "title": a.title, "snippet": a.snippet}
        for a in retrieval.articles
    ]
    completion = llm.complete_structured(
        system=DRAFT_SYSTEM,
        user=draft_user(
            subject=normalized.subject,
            body=body_redacted,
            decision=decision_result.decision.value,
            articles=articles,
            plan=plan,
        ),
        schema=Draft,
        model=settings.model_draft,
    )
    return completion.parsed, completion  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Stage 8 — Guardrails
# --------------------------------------------------------------------------- #
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (all|previous|prior) instructions",
        r"disregard (the )?(above|previous)",
        r"you are now",
        r"system prompt",
        r"reveal your (instructions|prompt)",
        r"call the .* tool",
        r"transfer .* funds",
    ]
]

_TONE_BAD = ["stupid", "idiot", "shut up", "ridiculous", "your fault"]


def check_injection(text: str) -> GuardrailCheck:
    hits = [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]
    return GuardrailCheck(
        name="injection",
        passed=not hits,
        detail=",".join(hits) if hits else "clean",
    )


def check_pii_leak(drafted_body: str) -> GuardrailCheck:
    leaked = contains_pii(drafted_body)
    return GuardrailCheck(
        name="pii_leak",
        passed=not leaked,
        detail="PII present in draft" if leaked else "clean",
    )


def check_groundedness(drafted: Draft, retrieved_ids: set[str]) -> GuardrailCheck:
    """A draft is grounded if every citation points at a retrieved article."""

    if not drafted.citations:
        # No citations: acceptable only if there was nothing to retrieve.
        return GuardrailCheck(
            name="groundedness",
            passed=True,
            detail="no citations",
            score=0.0,
        )
    bad = [c.article_id for c in drafted.citations if c.article_id not in retrieved_ids]
    score = 1.0 - (len(bad) / len(drafted.citations))
    return GuardrailCheck(
        name="groundedness",
        passed=not bad,
        detail=f"ungrounded citations: {bad}" if bad else "all citations grounded",
        score=round(score, 3),
    )


def check_tone(drafted_body: str) -> GuardrailCheck:
    low = drafted_body.lower()
    hits = [w for w in _TONE_BAD if w in low]
    return GuardrailCheck(
        name="tone",
        passed=not hits,
        detail=f"off-tone phrases: {hits}" if hits else "ok",
    )


def check_policy(decision_result: DecisionResult) -> GuardrailCheck:
    """Risk topics must be flagged for approval, and never auto-resolved."""

    risky = [t.value for t in decision_result.risk_topics if t.value not in {"none"}]
    if risky and decision_result.decision == Decision.AUTO_RESOLVE:
        return GuardrailCheck(
            name="policy",
            passed=False,
            detail=f"auto_resolve on risk topics {risky}",
        )
    if risky and not decision_result.requires_approval:
        return GuardrailCheck(
            name="policy",
            passed=False,
            detail=f"risk topics {risky} without approval flag",
        )
    return GuardrailCheck(name="policy", passed=True, detail="ok")


def run_guardrails(
    *,
    ticket_text: str,
    decision_result: DecisionResult,
    drafted: Draft | None,
    retrieved_ids: set[str],
) -> GuardrailReport:
    checks = [
        check_injection(ticket_text),
        check_policy(decision_result),
    ]
    if drafted is not None:
        checks += [
            check_pii_leak(drafted.body),
            check_groundedness(drafted, retrieved_ids),
            check_tone(drafted.body),
        ]
    injection = next(c for c in checks if c.name == "injection")
    pii = next((c for c in checks if c.name == "pii_leak"), None)
    grounded = next((c for c in checks if c.name == "groundedness"), None)
    return GuardrailReport(
        passed=all(c.passed for c in checks),
        checks=checks,
        injection_detected=not injection.passed,
        pii_leak_detected=bool(pii and not pii.passed),
        groundedness=grounded.score if grounded else None,
    )


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
