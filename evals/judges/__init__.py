"""LLM-as-judge, done properly.

A rubric with scored dimensions (not a vague "is this good?"), a judge model
different from the one under test, calibration against human labels, and
pairwise judging with position swapping for A/B runs.
"""

from evals.judges.calibration import agreement_report, cohens_kappa
from evals.judges.pairwise import pairwise_compare
from evals.judges.rubric import RUBRIC, JudgeScore, RubricJudge

__all__ = [
    "RUBRIC",
    "RubricJudge",
    "JudgeScore",
    "cohens_kappa",
    "agreement_report",
    "pairwise_compare",
]
