# ADR 0003 — Build the eval harness before the rest of the agent

**Status:** Accepted

## Context

Most teams build the agent, then bolt on evals, then discover they have no trustworthy way
to tell whether a prompt tweak helped or hurt. By then, no baseline exists and every change
is a guess.

## Decision

Stand up the eval harness (dataset, suites, runner, metrics, config, CLI) as soon as the
classifier exists — before the rest of the agent — and wire it into CI. Every later stage
is added *with* its evaluation.

## Consequences

- Every capability ships with its metric and a gate; changes are measurable from day one.
- Forces typed stage outputs early, which improves the orchestrator design.
- A held-out split can be protected from the start, avoiding eval leakage.
- Slower initial velocity; the payoff is that "better" is defined.

## Alternatives

- **Evals later** — the common path; leads to unevaluable prompts and no regression safety.
- **Only end-to-end tests** — no localization of failures.
