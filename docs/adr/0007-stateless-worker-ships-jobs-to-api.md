# ADR 0007 — Stateless worker ships jobs to the API runner

**Status:** Accepted

## Context

Tickets can be processed inline in the API or handed to a background worker. Running the
agent in the worker means the worker needs a DB pool and the full model/tool stack; giving
both processes their own connection pools multiplies connection management and can exhaust
Postgres under scale.

## Decision

Keep the worker **stateless**. It holds no DB pool. It calls
`POST /internal/worker` on the API process, which owns the pool and runs the agent. Jobs
use a deterministic `_job_id` for idempotency, retry with backoff, and land in a
`DeadLetterJob` row when retries are exhausted.

## Consequences

- One place owns DB connections; the worker is trivially scalable.
- The API's internal runner is the single execution path for both sync and async work —
  one code path to test and fix.
- The worker needs network access to the API; the two are co-deployed.

## Alternatives

- **Full agent in the worker** — duplicates pools and the model/tool stack, harder to keep
  consistent with the inline path.
- **No worker** — no backpressure or retry isolation; ingest latency tied to model latency.
