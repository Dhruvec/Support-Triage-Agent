"""Config-driven eval runs.

A run is fully described by a YAML file (``configs/v1.yaml``), so the exact
pipeline settings, model routing, judge mode, and gate thresholds that produced a
set of numbers are captured verbatim on the :class:`evals.storage.EvalRun` row.
That is what makes two runs comparable and a regression attributable to a change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from agent.config import Settings, get_settings
from agent.orchestrator import PipelineConfig

# Maps YAML model keys to ``Settings`` field names.
_MODEL_FIELDS = {
    "classify": "model_classify",
    "decide": "model_decide",
    "draft": "model_draft",
    "judge": "model_judge",
}


@dataclass
class GateThresholds:
    """Minimum acceptable values. A run fails CI if any check is below threshold."""

    escalation_recall: float = 0.90
    auto_resolve_precision: float = 0.85
    decision_accuracy: float = 0.75
    classification_accuracy: float = 0.70
    judge_pass_rate: float = 0.75
    max_cost_usd: float | None = None
    max_error_rate: float | None = 0.05

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvalConfig:
    name: str
    dataset_version: str = "v0"
    pipeline: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)
    judge: dict = field(default_factory=dict)
    gates: GateThresholds = field(default_factory=GateThresholds)

    # -- derived ---------------------------------------------------------- #
    def pipeline_config(self) -> PipelineConfig:
        p = self.pipeline or {}
        return PipelineConfig(
            prompt_version=str(p.get("prompt_version", "v1")),
            retrieval_k=int(p.get("retrieval_k", 5)),
            dedupe_threshold=float(p.get("dedupe_threshold", 0.92)),
            force_stub_llm=bool(p.get("force_stub_llm", False)),
        )

    @property
    def judge_force_heuristic(self) -> bool:
        return bool(self.judge.get("force_heuristic", False))

    @property
    def judge_model(self) -> str | None:
        return self.judge.get("model") or self.models.get("judge")

    def settings(self) -> Settings:
        """Return settings with per-run model routing applied (without mutating the cache)."""

        overrides = {
            field_name: self.models[key]
            for key, field_name in _MODEL_FIELDS.items()
            if key in self.models
        }
        if not overrides:
            return get_settings()
        return get_settings().model_copy(update=overrides)

    def snapshot(self) -> dict:
        d = asdict(self)
        d["pipeline"] = self.pipeline_config().snapshot()
        return d


def load_config(path: str | Path = "configs/v1.yaml") -> EvalConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"eval config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    gates_raw = raw.get("gates", {}) or {}
    known = set(GateThresholds.__dataclass_fields__)
    gates = GateThresholds(**{k: v for k, v in gates_raw.items() if k in known})
    return EvalConfig(
        name=str(raw.get("name", path.stem)),
        dataset_version=str(raw.get("dataset_version", "v0")),
        pipeline=raw.get("pipeline", {}) or {},
        models=raw.get("models", {}) or {},
        judge=raw.get("judge", {}) or {},
        gates=gates,
    )
