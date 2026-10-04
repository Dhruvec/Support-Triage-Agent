"""Unit tests for the eval metric computations (hand-computed expectations)."""

from __future__ import annotations

import pytest

from evals.metrics import classification as cls
from evals.metrics import decision as dec
from evals.metrics import retrieval as ret


# --------------------------------------------------------------------------- #
# Classification metrics
# --------------------------------------------------------------------------- #
def test_classification_metrics_hand_computed():
    metrics = cls.compute(["a", "b", "a"], ["a", "a", "a"])
    assert metrics.support == 3
    assert metrics.accuracy == pytest.approx(2 / 3)
    # per-class precision is rounded to 4 decimals by the implementation.
    assert metrics.per_class["a"]["precision"] == pytest.approx(0.6667)
    assert metrics.per_class["a"]["recall"] == pytest.approx(1.0)
    assert metrics.per_class["a"]["f1"] == pytest.approx(0.8)
    assert metrics.per_class["b"]["f1"] == 0.0
    assert metrics.macro_f1 == pytest.approx(0.4)


def test_classification_metrics_empty():
    metrics = cls.compute([], [])
    assert metrics.support == 0
    assert metrics.accuracy == 0.0


def test_classification_metrics_length_mismatch_raises():
    with pytest.raises(AssertionError):
        cls.compute(["a"], ["a", "b"])


# --------------------------------------------------------------------------- #
# Decision metrics
# --------------------------------------------------------------------------- #
def test_decision_metrics_hand_computed():
    pairs = [
        ("i1", True, True, "escalate"),
        ("i2", False, False, "auto_resolve"),
        ("i3", False, True, "escalate"),
        ("i4", True, False, "draft"),
    ]
    metrics = dec.compute(pairs)
    assert metrics.support == 4
    assert metrics.escalation_recall == pytest.approx(0.5)
    assert metrics.escalation_precision == pytest.approx(0.5)
    assert metrics.accuracy == pytest.approx(0.5)
    assert metrics.auto_resolve_precision == pytest.approx(1.0)
    assert metrics.auto_resolve_recall == pytest.approx(1.0)
    assert metrics.missed_escalations == ["i4"]
    assert metrics.false_escalations == ["i3"]


def test_decision_metrics_empty():
    metrics = dec.compute([])
    assert metrics.support == 0
    assert metrics.missed_escalations == []
    assert metrics.false_escalations == []


# --------------------------------------------------------------------------- #
# Retrieval metrics
# --------------------------------------------------------------------------- #
def test_retrieval_metrics_hand_computed():
    metrics = ret.compute([["x", "y", "z"], ["a"]], [{"y"}, {"a"}])
    assert metrics.support == 2
    assert metrics.recall_at_k == pytest.approx(1.0)
    assert metrics.mrr == pytest.approx(0.75)
    assert metrics.hit_rate == pytest.approx(1.0)


def test_retrieval_metrics_skips_empty_gold():
    metrics = ret.compute([["x"], ["a"]], [set(), {"a"}])
    assert metrics.support == 1
    assert metrics.mrr == pytest.approx(1.0)


def test_retrieval_metrics_empty():
    metrics = ret.compute([], [])
    assert metrics.support == 0
