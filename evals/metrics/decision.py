"""Decision metrics.

Escalation recall is the headline metric: missing a needed escalation is the
costly error. Auto-resolve precision measures how often we claim to have solved
something without a human.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DecisionMetrics:
    escalation_recall: float = 0.0
    escalation_precision: float = 0.0
    auto_resolve_precision: float = 0.0
    auto_resolve_recall: float = 0.0
    accuracy: float = 0.0
    missed_escalations: list[str] = None
    false_escalations: list[str] = None
    support: int = 0

    def to_dict(self) -> dict:
        return {
            "escalation_recall": round(self.escalation_recall, 4),
            "escalation_precision": round(self.escalation_precision, 4),
            "auto_resolve_precision": round(self.auto_resolve_precision, 4),
            "auto_resolve_recall": round(self.auto_resolve_recall, 4),
            "accuracy": round(self.accuracy, 4),
            "missed_escalations": self.missed_escalations or [],
            "false_escalations": self.false_escalations or [],
            "support": self.support,
        }


def compute(pairs: list[tuple[str, bool, bool, str]]) -> DecisionMetrics:
    """pairs: (item_id, gold_escalate, pred_escalate, pred_decision)."""

    n = len(pairs)
    if n == 0:
        return DecisionMetrics(missed_escalations=[], false_escalations=[])

    tp = fp = fn = tn = 0
    missed: list[str] = []
    false_esc: list[str] = []
    for item_id, gold_esc, pred_esc, _ in pairs:
        if gold_esc and pred_esc:
            tp += 1
        elif not gold_esc and pred_esc:
            fp += 1
            false_esc.append(item_id)
        elif gold_esc and not pred_esc:
            fn += 1
            missed.append(item_id)
        else:
            tn += 1

    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0

    # auto-resolve precision/recall (positive class = decided auto_resolve)
    ar_tp = sum(1 for _, g, p, d in pairs if (not g) and d == "auto_resolve" and (not p))
    ar_fp = sum(1 for _, g, p, d in pairs if g and d == "auto_resolve")
    ar_total_pred = sum(1 for _, _, _, d in pairs if d == "auto_resolve")
    ar_total_gold = sum(1 for _, g, p, _ in pairs if not g and not p)
    ar_precision = ar_tp / ar_total_pred if ar_total_pred else 1.0
    ar_recall = ar_tp / ar_total_gold if ar_total_gold else 1.0

    accuracy = sum(1 for _, g, p, _ in pairs if g == p) / n

    return DecisionMetrics(
        escalation_recall=recall,
        escalation_precision=precision,
        auto_resolve_precision=ar_precision,
        auto_resolve_recall=ar_recall,
        accuracy=accuracy,
        missed_escalations=missed,
        false_escalations=false_esc,
        support=n,
    )
