"""Tests for the eval harness: config, dataset, suites, gates, and a smoke run."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.corpus import kb_article_id
from evals.config import GateThresholds, load_config
from evals.dataset import EvalItem, Expected, filter_slices, load_items, splits
from evals.runner import (
    RunReport,
    _gold_escalate,
    _normalize_article_id,
    evaluate_gates,
    run_suite,
)
from evals.suites import get_suite

ROOT = Path(__file__).resolve().parent.parent
V1_CONFIG = str(ROOT / "configs" / "v1.yaml")
CI_CONFIG = str(ROOT / "configs" / "ci.yaml")


def _item(item_id: str, slc: str, split: str = "dev") -> EvalItem:
    return EvalItem(
        id=item_id,
        dataset_version="v0",
        slice=slc,
        subject="s",
        body="b",
        expected=Expected(),
        split=split,
    )


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_load_v1_config():
    cfg = load_config(V1_CONFIG)
    assert cfg.name == "v1"
    assert cfg.dataset_version == "v0"
    assert cfg.judge_force_heuristic is True
    assert cfg.pipeline_config().force_stub_llm is True
    assert cfg.gates.escalation_recall == pytest.approx(0.90)


def test_load_ci_config_gates():
    cfg = load_config(CI_CONFIG)
    assert cfg.name == "ci"
    assert cfg.gates.escalation_recall == pytest.approx(0.85)
    assert cfg.gates.auto_resolve_precision == pytest.approx(0.95)


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #
def test_load_items_has_stable_ids():
    items = load_items()
    assert items
    ids = [it.id for it in items]
    assert len(ids) == len(set(ids))
    assert all(it.dataset_version for it in items)


def test_splits_and_filter_slices():
    items = [
        _item("a", "happy_path"),
        _item("b", "angry"),
        _item("c", "happy_path", split="test"),
    ]
    grouped = splits(items)
    assert len(grouped["dev"]) == 2
    assert len(grouped["test"]) == 1
    assert [it.id for it in filter_slices(items, ["angry"])] == ["b"]
    assert filter_slices(items, None) == items


def test_gold_escalate_prefers_explicit_flag():
    explicit = EvalItem("x", "v0", "s", "", "", Expected(escalate=True, decision="draft"))
    inferred = EvalItem("y", "v0", "s", "", "", Expected(decision="escalate"))
    assert _gold_escalate(explicit) is True
    assert _gold_escalate(inferred) is True


# --------------------------------------------------------------------------- #
# Suites
# --------------------------------------------------------------------------- #
def test_smoke_suite_selects_expected_slices():
    suite = get_suite("smoke")
    assert suite.slices == ("happy_path", "angry", "vague")
    items = [_item("a", "happy_path"), _item("b", "legal"), _item("c", "angry")]
    assert [it.id for it in suite.select(items)] == ["a", "c"]


def test_regression_suite_selects_all():
    suite = get_suite("regression")
    items = [_item("a", "happy_path"), _item("b", "legal")]
    assert len(suite.select(items)) == 2


def test_unknown_suite_raises():
    with pytest.raises(KeyError):
        get_suite("does-not-exist")


# --------------------------------------------------------------------------- #
# Article id normalization
# --------------------------------------------------------------------------- #
def test_normalize_article_id_accepts_slug_or_hash():
    assert _normalize_article_id("kb-abc123") == "kb-abc123"
    assert _normalize_article_id("reset-password") == kb_article_id("reset-password")
    assert _normalize_article_id("unknown-slug") == "unknown-slug"


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def _good_metrics() -> dict:
    return {
        "classification": {"category": {"accuracy": 0.8}},
        "decision": {
            "escalation_recall": 0.95,
            "auto_resolve_precision": 0.9,
            "accuracy": 0.8,
        },
        "judge": {"pass_rate": 0.9},
        "ops": {"total_cost_usd": 0.0, "error_rate": 0.0},
    }


def test_evaluate_gates_all_pass():
    result = evaluate_gates(GateThresholds(), _good_metrics())
    assert result.passed is True
    assert all(c.passed for c in result.checks)


def test_evaluate_gates_fails_low_escalation_recall():
    metrics = _good_metrics()
    metrics["decision"]["escalation_recall"] = 0.10
    result = evaluate_gates(GateThresholds(), metrics)
    assert result.passed is False
    failing = {c.name for c in result.checks if not c.passed}
    assert "escalation_recall" in failing


def test_evaluate_gates_skips_none_threshold():
    metrics = _good_metrics()
    metrics["ops"]["total_cost_usd"] = 999.0
    result = evaluate_gates(GateThresholds(max_cost_usd=None), metrics)
    cost_check = next(c for c in result.checks if c.name == "max_cost_usd")
    assert cost_check.passed is True
    assert cost_check.detail == "skipped"


def test_evaluate_gates_fails_cost_over_budget():
    metrics = _good_metrics()
    metrics["ops"]["total_cost_usd"] = 10.0
    result = evaluate_gates(GateThresholds(max_cost_usd=5.0), metrics)
    cost_check = next(c for c in result.checks if c.name == "max_cost_usd")
    assert cost_check.passed is False


# --------------------------------------------------------------------------- #
# Full offline smoke run (stub LLM + heuristic judge, no DB, no network)
# --------------------------------------------------------------------------- #
def test_run_suite_smoke_offline():
    report = run_suite("smoke", V1_CONFIG, persist=False)
    assert isinstance(report, RunReport)
    assert report.suite == "smoke"
    assert report.num_items > 0
    assert "decision" in report.metrics
    assert "category" in report.metrics["classification"]
    assert report.gates.checks
