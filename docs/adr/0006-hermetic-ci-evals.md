# ADR 0006 — Hermetic offline evals in CI

**Status:** Accepted

## Context

Eval gates must run on every PR, deterministically, without secrets or flaky network
calls. Calling real models in CI is slow, costs money, needs `ANTHROPIC_API_KEY`, and can
fail for reasons unrelated to the change.

## Decision

Provide a deterministic `StubClient` LLM and a `HashingEmbedder`, and a heuristic rubric
judge, selected by `configs/ci.yaml` (`force_stub_llm`, `force_heuristic`). CI runs the
smoke/adversarial/regression suites fully offline against SQLite; real-model configs
(`configs/v1.yaml`) are the release gate, run separately/on demand.

## Consequences

- Gates are fast, free, secret-free, and reproducible — a red gate always means the change.
- Stub behavior is not real-model behavior; CI floors are set below the stub baseline and
  are a regression guard, not a quality target.
- The `ToolClient` protocol must have an offline implementation, which the harness also
  uses for retrieval metrics.

## Alternatives

- **Real models in CI** — slow, costly, needs secrets, nondeterministic gates.
- **No CI evals** — regressions only found in production.
