"""Suite composition.

A *suite* is a named selection of dataset slices used to answer one question:

* ``smoke``        — fast, must-pass on every PR (happy path + obvious risks).
* ``regression``   — the full golden set; catches quality drift over time.
* ``adversarial``  — injection/PII/out-of-policy/no-KB attacks; safety-critical.

Suites are defined here as data so the CLI, the CI workflow, and the runner all
agree on what "the smoke suite" means. Slice names map to the ``slice`` field in
:mod:`evals.dataset`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SuiteDefinition:
    name: str
    description: str
    # Empty ``slices`` means "all slices" (used by the regression suite).
    slices: tuple[str, ...] = field(default_factory=tuple)

    def select(self, items):
        if not self.slices:
            return list(items)
        wanted = set(self.slices)
        return [it for it in items if it.slice in wanted]


SUITES: dict[str, SuiteDefinition] = {
    "smoke": SuiteDefinition(
        name="smoke",
        description="Fast must-pass gate: happy path plus clear escalation risks.",
        slices=("happy_path", "angry", "vague"),
    ),
    "regression": SuiteDefinition(
        name="regression",
        description="Full golden set across every slice; detects quality drift.",
        slices=(),
    ),
    "adversarial": SuiteDefinition(
        name="adversarial",
        description="Injection, PII, out-of-policy refunds, no-KB, legal threats.",
        slices=(
            "adversarial_injection",
            "adversarial_pii",
            "adversarial_refund",
            "adversarial_no_kb",
            "legal",
        ),
    ),
}


def get_suite(name: str) -> SuiteDefinition:
    try:
        return SUITES[name]
    except KeyError as exc:  # pragma: no cover - guarded by CLI choices
        raise KeyError(f"unknown suite '{name}'; known: {sorted(SUITES)}") from exc


def all_slice_names() -> list[str]:
    return sorted(
        {
            "happy_path",
            "angry",
            "vague",
            "multilingual",
            "multi_issue",
            "duplicate",
            "outage",
            "legal",
            "adversarial_injection",
            "adversarial_pii",
            "adversarial_refund",
            "adversarial_no_kb",
        }
    )
