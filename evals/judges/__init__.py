"""LLM-as-judge, done properly.

A rubric with scored dimensions (not a vague "is this good?"), a judge model
different from the one under test, calibration against human labels, and
pairwise judging with position swapping for A/B runs.
"""

from evals.judges.rubric import RUBRIC, RubricJudge, JudgeScore
from evals.judges.calibration import cohens_kappa, agreement_report
from evals.judges.pairwise import pairwise_compare

__all__ = [
    "RUBRIC",
    "RubricJudge",
    "JudgeScore",
    "cohens_kappa",
    "agreement_report",
    "pairwise_compare",
]
