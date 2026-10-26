"""Classification metrics: accuracy, per-class P/R/F1, confusion matrix."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClassificationMetrics:
    accuracy: float = 0.0
    macro_f1: float = 0.0
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    support: int = 0

    def to_dict(self) -> dict:
        return {
            "accuracy": round(self.accuracy, 4),
            "macro_f1": round(self.macro_f1, 4),
            "per_class": self.per_class,
            "confusion": self.confusion,
            "support": self.support,
        }


def compute(
    gold: list[str], pred: list[str], labels: list[str] | None = None
) -> ClassificationMetrics:
    assert len(gold) == len(pred)
    n = len(gold)
    if n == 0:
        return ClassificationMetrics()

    label_set = sorted(labels or (set(gold) | set(pred)))
    confusion: dict[str, dict[str, int]] = {
        g: {p: 0 for p in label_set} for g in label_set
    }
    for g, p in zip(gold, pred, strict=False):
        confusion.setdefault(g, {p_: 0 for p_ in label_set})
        confusion[g][p] = confusion[g].get(p, 0) + 1

    per_class: dict[str, dict[str, float]] = {}
    f1s: list[float] = []
    for label in label_set:
        tp = sum(1 for g, p in zip(gold, pred, strict=False) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, pred, strict=False) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, pred, strict=False) if g == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        support = sum(1 for g in gold if g == label)
        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
        f1s.append(f1)

    accuracy = sum(1 for g, p in zip(gold, pred, strict=False) if g == p) / n
    return ClassificationMetrics(
        accuracy=accuracy,
        macro_f1=sum(f1s) / len(f1s) if f1s else 0.0,
        per_class=per_class,
        confusion=confusion,
        support=n,
    )


def per_slice(pairs: dict[str, list[tuple[str, str]]]) -> dict[str, dict]:
    """pairs: slice -> list of (gold, pred)."""

    out: dict[str, dict] = {}
    for slc, plist in pairs.items():
        gold = [g for g, _ in plist]
        pred = [p for _, p in plist]
        out[slc] = compute(gold, pred).to_dict()
    return out
