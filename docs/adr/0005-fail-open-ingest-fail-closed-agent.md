# ADR 0005 — Fail-open ingest, fail-closed agent

**Status:** Accepted

## Context

Two failure surfaces exist: getting a ticket *in*, and deciding what to *do* with it. The
right behavior on failure differs for each.

## Decision

- **Ingest is fail-open.** If the queue (Redis) is down, `POST /ingest?async=true` runs the
  agent inline rather than dropping the ticket. If a row was persisted but the agent never
  finished (retry / crash), a subsequent run **re-runs** it instead of reporting a null
  outcome. A ticket is never silently dropped.
- **The agent is fail-closed.** Any LLM or tool failure, low confidence, or failed
  guardrail escalates to a human. Refunds, account changes, and legal topics always require
  approval and are never auto-executed.

## Consequences

- No lost tickets even during partial outages.
- No risky autonomous action on uncertainty; the cost is more escalations.
- Requires idempotency to make re-runs safe (`_job_id`, idempotency key).

## Alternatives

- **Fail-closed ingest** — simple, but drops tickets when Redis is down.
- **Fail-open agent** — auto-resolve on error; dangerous and rejected.
