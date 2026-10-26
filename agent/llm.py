"""LLM client with structured outputs and a deterministic offline stub.

Two clients implement the same tiny interface:

* :class:`AnthropicClient` — real calls via the Anthropic SDK, forced to emit
  JSON matching a Pydantic schema.
* :class:`StubClient` — deterministic, rule-based outputs. This is what makes
  the eval harness runnable in CI with no API key and no network, and it keeps
  the harness itself testable. Stub behaviour is intentionally simple; it is a
  baseline, not a competitor to a real model.

Every call returns a :class:`Completion` carrying the parsed object plus token
and cost accounting so each stage can be measured (tokens/cost per ticket).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from agent.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

# Rough public prices (USD per 1M tokens) for cost accounting only.
_PRICES = {
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
}
_DEFAULT_PRICE = (3.00, 15.00)


@dataclass
class Completion:
    parsed: BaseModel
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = _PRICES.get(model, _DEFAULT_PRICE)
    return (input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out


def _extract_json(text: str) -> dict:
    """Parse JSON from a model response, tolerating fenced code blocks."""

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


class LLMClient:
    """Interface implemented by the real and stub clients."""

    name = "base"

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        model: str,
        max_tokens: int = 1024,
    ) -> Completion:  # pragma: no cover - interface
        raise NotImplementedError


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for AnthropicClient")
        import anthropic

        self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        model: str,
        max_tokens: int = 1024,
    ) -> Completion:
        json_schema = schema.model_json_schema()
        tool_name = f"emit_{schema.__name__.lower()}"
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": f"Return a {schema.__name__} object.",
                    "input_schema": json_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        text = ""
        payload: dict[str, Any] = {}
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                payload = block.input  # type: ignore[assignment]
            elif getattr(block, "type", None) == "text":
                text += block.text
        if not payload:
            payload = _extract_json(text)
        parsed = schema.model_validate(payload)
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        return Completion(
            parsed=parsed,
            text=json.dumps(payload),
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok),
            raw=payload,
        )


class StubClient(LLMClient):
    """Deterministic, keyword-driven client for offline harness runs.

    Each ``schema`` maps to a small rule engine producing a plausible,
    schema-valid object. Outputs are stable across runs (seeded by the input
    hash) so eval regression diffs are meaningful without an API key.
    """

    name = "stub"

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        model: str,
        max_tokens: int = 1024,
    ) -> Completion:
        handler = _STUB_HANDLERS.get(schema.__name__)
        payload = handler(user) if handler else {}
        parsed = schema.model_validate(payload)
        in_tok = len(user.split())
        out_tok = len(json.dumps(payload, default=str).split())
        return Completion(
            parsed=parsed,
            text=json.dumps(payload, default=str),
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=0.0,
            raw=payload,
        )


# --------------------------------------------------------------------------- #
# Stub rule engines
# --------------------------------------------------------------------------- #
def _hash_unit(text: str, salt: str = "") -> float:
    digest = hashlib.sha256((salt + text).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _stub_classify(user: str) -> dict:
    from agent.taxonomy import Category, Language, Priority, Sentiment

    low = user.lower()
    keyword_categories = {
        Category.REFUND: ["refund", "money back", "reimburse"],
        Category.BILLING: ["charge", "invoice", "billing", "charged twice", "overcharge"],
        Category.SHIPPING: ["shipping", "delivery", "tracking", "package", "parcel"],
        Category.ACCOUNT: ["password", "login", "log in", "account", "2fa", "reset"],
        Category.TECHNICAL: ["error", "bug", "crash", "not working", "broken", "500"],
        Category.CANCELLATION: ["cancel", "cancellation", "unsubscribe"],
        Category.PRODUCT: ["feature", "how do i", "how to", "question"],
    }
    category = Category.OTHER
    for cat, kws in keyword_categories.items():
        if any(k in low for k in kws):
            category = cat
            break

    angry = any(w in low for w in ["furious", "unacceptable", "ridiculous", "angry", "terrible"])
    negative = any(w in low for w in ["issue", "problem", "wrong", "broken", "fail", "hate"])
    positive = any(w in low for w in ["thanks", "thank you", "great", "love"])
    sentiment = (
        Sentiment.ANGRY
        if angry
        else Sentiment.NEGATIVE
        if negative
        else Sentiment.POSITIVE
        if positive
        else Sentiment.NEUTRAL
    )

    urgent = any(w in low for w in ["urgent", "asap", "outage", "down", "immediately"])
    priority = Priority.P0 if ("outage" in low or "down for everyone" in low) else (
        Priority.P1 if urgent else Priority.P2
    )

    conf = round(0.55 + 0.4 * _hash_unit(user, "classify"), 2)
    return {
        "category": category.value,
        "priority": priority.value,
        "sentiment": sentiment.value,
        "language": Language.EN.value,
        "confidence": min(conf, 0.97),
        "rationale": "stub: keyword match",
    }


def _stub_decide(user: str) -> dict:
    from agent.taxonomy import Decision, RiskTopic

    low = user.lower()
    risk: list[str] = []
    if any(k in low for k in ["refund", "money back", "reimburse"]):
        risk.append(RiskTopic.REFUND.value)
    account_kws = ["change my email", "close my account", "delete account", "change plan"]
    if any(k in low for k in account_kws):
        risk.append(RiskTopic.ACCOUNT_CHANGE.value)
    if any(k in low for k in ["lawyer", "legal", "sue", "attorney", "gdpr"]):
        risk.append(RiskTopic.LEGAL.value)

    no_answer = any(k in low for k in ["outage", "data loss", "everyone is down"])
    escalate = bool(risk) or no_answer
    decision = Decision.ESCALATE if escalate else Decision.AUTO_RESOLVE
    if not escalate and any(k in low for k in ["how do i", "how to", "question", "feature"]):
        decision = Decision.DRAFT

    return {
        "decision": decision.value,
        "confidence": round(0.5 + 0.4 * _hash_unit(user, "decide"), 2),
        "reasons": ["stub decision"] + [f"risk:{r}" for r in risk],
        "risk_topics": risk or [RiskTopic.NONE.value],
        "requires_approval": bool(risk),
    }


def _stub_draft(user: str) -> dict:
    return {
        "body": (
            "Thanks for reaching out. Based on our help center, here is what you can do: "
            "follow the guided steps in the linked article. If that does not resolve it, "
            "reply and we will escalate to a specialist."
        ),
        "citations": [],
        "internal_note": "stub draft generated offline",
    }


_STUB_HANDLERS = {
    "Classification": _stub_classify,
    "DecisionResult": _stub_decide,
    "Draft": _stub_draft,
}


def get_llm_client(settings: Settings | None = None, force_stub: bool = False) -> LLMClient:
    """Return the real client when configured, else the deterministic stub."""

    settings = settings or get_settings()
    if force_stub or not settings.anthropic_api_key:
        return StubClient()
    return AnthropicClient(settings)
