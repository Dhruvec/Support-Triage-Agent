"""Canonical taxonomy and enums shared across the pipeline and evals.

Keeping the label vocabularies in one module is what lets the classifier,
the eval metrics, and the golden dataset agree on a single set of categories.
Changing a value here is a breaking change for stored datasets and runs.
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    BILLING = "billing"
    REFUND = "refund"
    ACCOUNT = "account"
    TECHNICAL = "technical"
    SHIPPING = "shipping"
    PRODUCT = "product"
    CANCELLATION = "cancellation"
    OTHER = "other"


class Priority(StrEnum):
    P0 = "p0"  # outage / severe, immediate human attention
    P1 = "p1"  # high
    P2 = "p2"  # normal
    P3 = "p3"  # low


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class Language(StrEnum):
    EN = "en"
    ES = "es"
    FR = "fr"
    DE = "de"
    PT = "pt"
    HI = "hi"
    UNKNOWN = "unknown"


class Decision(StrEnum):
    """The three terminal decisions of the pipeline."""

    AUTO_RESOLVE = "auto_resolve"
    DRAFT = "draft"
    ESCALATE = "escalate"


class RiskTopic(StrEnum):
    """Topics that always require human approval before any write action."""

    REFUND = "refund"
    ACCOUNT_CHANGE = "account_change"
    LEGAL = "legal"
    PII = "pii"
    NONE = "none"


class ActionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


CATEGORY_VALUES = [c.value for c in Category]
PRIORITY_VALUES = [p.value for p in Priority]
SENTIMENT_VALUES = [s.value for s in Sentiment]
DECISION_VALUES = [d.value for d in Decision]
