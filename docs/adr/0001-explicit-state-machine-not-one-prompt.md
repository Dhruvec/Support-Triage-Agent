# ADR 0001 — Explicit state-machine pipeline instead of one giant prompt

**Status:** Accepted

## Context

The agent must classify, dedupe, retrieve, decide, draft, and act, with safety gates in
between. The tempting shortcut is a single large prompt that "does triage". That is fast to
write and impossible to evaluate: a wrong answer gives no signal about which capability
failed, and you cannot put a cheap model on classification and a strong one on drafting.

## Decision

Model the pipeline as an explicit state machine (`agent/orchestrator.py`) with one typed
output schema per stage (`agent/schemas.py`). Stages run in order, each writing its result
to a `RunRecorder`. Rules (confidence thresholds, guardrails) live between stages, not
inside a prompt.

## Consequences

- Every stage is independently evaluable → per-layer metrics and localized regressions.
- Model routing is per stage (cheap classify, stronger decide/draft/judge).
- A run is a sequence of inspectable records (`agent_runs` / `stage_results`) → tracing.
- More moving parts than one prompt; the orchestrator is the seam to test.

## Alternatives

- **One prompt** — unevaluable, no model routing, no place to put hard safety gates.
- **A framework's "agent loop"** — hides the control flow that we specifically need to
  evaluate and constrain.
