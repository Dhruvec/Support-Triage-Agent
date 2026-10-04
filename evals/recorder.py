"""In-memory run recorder for the eval harness.

The orchestrator writes every stage through a :class:`RunRecorder`. In
production that is :class:`agent.orchestrator.DBRecorder` (Postgres). The harness
cannot use Postgres, so it uses :class:`EvalRecorder`, which collects the same
information (stage outputs, tokens, cost, latency, errors) in memory so per-stage
metrics and trajectory analysis are possible without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.orchestrator import PlannedAction
from agent.schemas import PipelineState


@dataclass
class StageRecord:
    stage: str
    output: dict
    model: str | None = None
    tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None
    input_hash: str = ""


@dataclass
class EvalRecorder:
    """Collects stage results for a single item run."""

    run_id: str = "eval-run"
    stages: list[StageRecord] = field(default_factory=list)
    pending: list[PlannedAction] = field(default_factory=list)
    finished: bool = False
    error: str | None = None

    def start_run(self, state: PipelineState, config: dict) -> str:
        self.stages.clear()
        self.pending.clear()
        self.finished = False
        self.error = None
        return self.run_id

    def record_stage(
        self,
        run_id: str,
        stage: str,
        output: dict,
        *,
        model: str | None = None,
        tokens: int = 0,
        cost: float = 0.0,
        latency_ms: float = 0.0,
        error: str | None = None,
        input_hash: str = "",
    ) -> None:
        self.stages.append(
            StageRecord(
                stage=stage,
                output=output,
                model=model,
                tokens=tokens,
                cost=cost,
                latency_ms=latency_ms,
                error=error,
                input_hash=input_hash,
            )
        )

    def finish_run(self, run_id: str, state: PipelineState, error: str | None) -> None:
        self.finished = True
        self.error = error

    def record_pending(self, state: PipelineState, action: PlannedAction) -> None:
        self.pending.append(action)

    # -- derived --------------------------------------------------------- #
    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.stages)

    @property
    def total_cost(self) -> float:
        return sum(s.cost for s in self.stages)

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.stages)

    def stage_names(self) -> list[str]:
        return [s.stage for s in self.stages]

    def errors(self) -> list[str]:
        return [f"{s.stage}: {s.error}" for s in self.stages if s.error]
