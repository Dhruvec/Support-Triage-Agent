"""Unit tests for deterministic PII redaction."""

from __future__ import annotations

from agent.pii import contains_pii, redact


def test_redact_email():
    result = redact("Email me at jane.doe@example.com.")
    assert result.had_pii is True
    assert result.redactions.get("email") == 1
    assert "[EMAIL]" in result.body_redacted


def test_redact_phone():
    result = redact("Call me at (555) 123-4567 please")
    assert result.redactions.get("phone") == 1
    assert "[PHONE]" in result.body_redacted


def test_redact_ssn():
    result = redact("My SSN is 123-45-6789")
    assert result.redactions.get("ssn") == 1
    assert "[SSN]" in result.body_redacted


def test_redact_card():
    result = redact("Card 4111-1111-1111-1111 on file")
    assert result.redactions.get("card") == 1
    assert "[CARD]" in result.body_redacted


def test_redact_iban():
    result = redact("IBAN DE89ABCDEFGHIJKL1234 here")
    assert result.redactions.get("iban") == 1
    assert "[IBAN]" in result.body_redacted


def test_redact_clean_text_has_no_pii():
    result = redact("I cannot log in to my account")
    assert result.had_pii is False
    assert result.redactions == {}
    assert result.body_redacted == "I cannot log in to my account"


def test_contains_pii_true_and_false():
    assert contains_pii("reach me at a@b.com") is True
    assert contains_pii("nothing sensitive at all") is False
