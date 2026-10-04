"""Rubric-based reply judge.

Dimensions are scored 1-5 with explicit anchors. The judge is a *different*
model from the one under test (configured per run). The judge can run
model-backed or deterministically (rubric-lite heuristics) so CI works offline;
the chosen mode is recorded in the run config so numbers are never compared
across incompatible judge modes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agent.config import get_settings
from agent.llm import LLMClient, get_llm_client

# The rubric: dimension -> (weight, anchor description).
RUBRIC: dict[str, tuple[float, str]] = {
    "groundedness": (
        0.35,
        "5 = every claim traceable to a cited KB article; 1 = claims unsupported or hallucinated",
    ),
    "policy_compliance": (
        0.25,
        "5 = respects refund/escalation policy; 1 = promises or acts outside policy",
    ),
    "completeness": (
        0.20,
        "5 = fully answers the customer's question; 1 = ignores the actual ask",
    ),
    "tone": (
        0.20,
        "5 = professional, empathetic, appropriate; 1 = rude or dismissive",
    ),
}


@dataclass
class JudgeScore:
    dimensions: dict[str, int] = field(default_factory=dict)
    weighted: float = 0.0
    rationale: str = ""
    judge_model: str = "heuristic"
    pass_threshold: float = 3.5

    @property
    def passed(self) -> bool:
        return self.weighted >= self.pass_threshold

    def to_dict(self) -> dict:
        d = asdict(self)
        d["passed"] = self.passed
        return d


_JUDGE_SYSTEM = """You are a strict support-reply evaluator. Score the draft on four
dimensions, each 1-5, using the anchors. Respond as JSON:
{"groundedness":int,"policy_compliance":int,"completeness":int,"tone":int,"rationale":str}
Only count claims as grounded if supported by the provided KB articles."""


class RubricJudge:
    def __init__(self, model: str | None = None, force_heuristic: bool = False) -> None:
        self.settings = get_settings()
        self.model = model or self.settings.model_judge
        self.force_heuristic = force_heuristic or not self.settings.anthropic_api_key
        self._llm: LLMClient | None = None

    def _llm_client(self) -> LLMClient:
        if self._llm is None:
            self._llm = get_llm_client(self.settings)
        return self._llm

    def score(
        self,
        *,
        ticket_text: str,
        draft_body: str,
        kb_snippets: list[str],
        decision: str,
    ) -> JudgeScore:
        if self.force_heuristic:
            return self._heuristic(ticket_text, draft_body, kb_snippets, decision)

        from agent.schemas import JudgeRawScore  # local import to avoid cycles

        user = (
            f"TICKET:\n{ticket_text}\n\nKB ARTICLES:\n" + "\n---\n".join(kb_snippets)
            + f"\n\nDECISION: {decision}\n\nDRAFT REPLY:\n{draft_body}"
        )
        completion = self._llm_client().complete_structured(
            system=_JUDGE_SYSTEM, user=user, schema=JudgeRawScore, model=self.model
        )
        raw = completion.parsed
        dims = {
            "groundedness": raw.groundedness,
            "policy_compliance": raw.policy_compliance,
            "completeness": raw.completeness,
            "tone": raw.tone,
        }
        return self._finalize(dims, raw.rationale, self.model)

    def _finalize(self, dims: dict[str, int], rationale: str, model: str) -> JudgeScore:
        weighted = sum(RUBRIC[d][0] * dims.get(d, 1) for d in RUBRIC)
        return JudgeScore(
            dimensions=dims,
            weighted=round(weighted, 3),
            rationale=rationale,
            judge_model=model,
        )

    def _heuristic(
        self, ticket_text: str, draft_body: str, kb_snippets: list[str], decision: str
    ) -> JudgeScore:
        """Deterministic rubric-lite used offline / in CI.

        Scores are crude but stable, which is what CI needs to detect *regressions*
        (a change that drops groundedness) without an API key.
        """

        body_low = draft_body.lower()
        # groundedness: does the draft reference any KB content token overlap?
        kb_tokens = set(" ".join(kb_snippets).lower().split())
        draft_tokens = set(body_low.split())
        overlap = len(kb_tokens & draft_tokens) / max(len(draft_tokens), 1)
        groundedness = 5 if overlap > 0.3 else 4 if overlap > 0.15 else 2

        # policy: risky decisions must not read as firm promises
        risky = decision in {"escalate"} or "refund" in ticket_text.lower()
        promises = any(w in body_low for w in ["i have refunded", "i approve", "your refund is"])
        policy = 1 if (risky and promises) else 5

        # completeness: length proxy + mentions of the ask
        completeness = 5 if len(draft_body.split()) >= 25 else 3 if draft_body.strip() else 1

        # tone: penalise known bad phrases
        bad = any(w in body_low for w in ["stupid", "ridiculous", "your fault", "shut up"])
        tone = 1 if bad else 5

        dims = {
            "groundedness": groundedness,
            "policy_compliance": policy,
            "completeness": completeness,
            "tone": tone,
        }
        return self._finalize(dims, "heuristic rubric-lite", "heuristic")
