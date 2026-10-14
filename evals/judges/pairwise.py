"""Pairwise judging with position swapping, for A/B comparisons."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PairwiseResult:
    wins_a: int = 0
    wins_b: int = 0
    ties: int = 0
    flipped: int = 0  # position-order flips (a sign of judge instability)

    def to_dict(self) -> dict:
        total = self.wins_a + self.wins_b + self.ties
        return {
            "wins_a": self.wins_a,
            "wins_b": self.wins_b,
            "ties": self.ties,
            "flipped": self.flipped,
            "win_rate_a": round(self.wins_a / total, 4) if total else 0.0,
            "win_rate_b": round(self.wins_b / total, 4) if total else 0.0,
            "position_flip_rate": round(self.flipped / total, 4) if total else 0.0,
        }


def pairwise_compare(
    *,
    outputs_a: list[str],
    outputs_b: list[str],
    judge_fn,
) -> PairwiseResult:
    """judge_fn(text_first, text_second) -> 'first' | 'second' | 'tie'.

    Each pair is judged twice with the order swapped. Only stable verdicts are
    counted; order flips are tallied separately as a judge-noise signal.
    """

    result = PairwiseResult()
    for a, b in zip(outputs_a, outputs_b, strict=False):
        first_pass = judge_fn(a, b)  # (a=first, b=second)
        second_pass = judge_fn(b, a)  # swapped

        # Normalise the swapped pass back to "a vs b".
        if second_pass == "first":
            swapped = "second"
        elif second_pass == "second":
            swapped = "first"
        else:
            swapped = "tie"

        if first_pass != swapped:
            result.flipped += 1
            result.ties += 1
            continue

        if first_pass == "first":
            result.wins_a += 1
        elif first_pass == "second":
            result.wins_b += 1
        else:
            result.ties += 1
    return result
