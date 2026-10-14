"""PII redaction.

Ticket text is untrusted input. Before any text reaches the LLM it is scrubbed
of emails, phone numbers, card numbers, and government IDs. Redaction is
deterministic (regex) plus an optional small-model pass for names/addresses.

The redaction map (entity -> count) is retained for metrics and for the PII
leak guardrail, but the raw values are never logged.
"""

from __future__ import annotations

import re

from agent.schemas import RedactionResult

_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"(?:(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4})"),
    "card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
}

_PLACEHOLDER = {
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "card": "[CARD]",
    "ssn": "[SSN]",
    "iban": "[IBAN]",
}


def redact(text: str) -> RedactionResult:
    """Return redacted text plus a count of each PII type removed."""

    redactions: dict[str, int] = {}
    redacted = text
    for name, pattern in _PATTERNS.items():
        matches = pattern.findall(redacted)
        if matches:
            redactions[name] = len(matches)
            redacted = pattern.sub(_PLACEHOLDER[name], redacted)
    return RedactionResult(
        body_redacted=redacted,
        redactions=redactions,
        had_pii=bool(redactions),
    )


def contains_pii(text: str) -> bool:
    """True if any configured PII pattern is present (used by guardrails)."""

    return any(p.search(text) for p in _PATTERNS.values())
