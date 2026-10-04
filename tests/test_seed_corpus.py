"""Verifies apps.api.seed consumes the shared corpus (no duplicated text).

This guards the refactor in which the seeder and the offline eval harness draw
from the single source of truth in :mod:`agent.corpus`.
"""

from __future__ import annotations

from agent import corpus
from apps.api import seed


def test_seed_uses_shared_corpus_objects():
    assert seed.KB_ARTICLES is corpus.KB_ARTICLES
    assert seed.POLICIES is corpus.POLICIES
    assert seed.TENANT_ID is corpus.TENANT_ID


def test_sample_tickets_shape():
    assert seed.SAMPLE_TICKETS
    for ticket in seed.SAMPLE_TICKETS:
        assert {"subject", "body", "customer_id"}.issubset(ticket)
