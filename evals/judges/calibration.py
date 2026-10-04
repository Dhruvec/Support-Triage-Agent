"""Judge calibration against human labels (Cohen's kappa)."""

from __future__ import annotations

from dataclasses import dataclass


def cohens_kappa(a: list[int], b: list[int]) -> float:
    """Cohen's kappa for two raters over categorical (here: binarised) labels."""

    assert len(a) == len(b) and a, "need non-empty, equal-length label lists"
    n = len(a)
    labels = set(a) | set(b)
    po = sum(1 for x, y in zip(a, b, strict=False) if x == y) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def binarise(scores: list[float], threshold: float = 3.5) -> list[int]:
    return [1 if s >= threshold else 0 for s in scores]


@dataclass
class AgreementReport:
    n: int
    raw_agreement: float
    kappa: float
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "raw_agreement": round(self.raw_agreement, 4),
            "kappa": round(self.kappa, 4),
            "interpretation": self.interpretation,
        }


def _interpret(kappa: float) -> str:
    if kappa < 0:
        return "poor (worse than chance) - fix the rubric before trusting numbers"
    if kappa < 0.21:
        return "slight - fix the rubric"
    if kappa < 0.41:
        return "fair - rubric needs work"
    if kappa < 0.61:
        return "moderate"
    if kappa < 0.81:
        return "substantial"
    return "almost perfect"


def agreement_report(judge_scores: list[float], human_scores: list[float], threshold: float = 3.5) -> AgreementReport:
    j = binarise(judge_scores, threshold)
    h = binarise(human_scores, threshold)
    raw = sum(1 for x, y in zip(j, h, strict=False) if x == y) / max(len(j), 1)
    kappa = cohens_kappa(j, h)
    return AgreementReport(n=len(j), raw_agreement=raw, kappa=kappa, interpretation=_interpret(kappa))
