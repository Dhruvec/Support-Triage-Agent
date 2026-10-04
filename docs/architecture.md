# Architecture

This document describes the components, the request and agent flows, and the data model.
For the decisions behind the design, see the [ADRs](adr/).

## Component map

```
Ticket sources ─┐
  email         │
  webhook       ├──▶ apps/api (FastAPI) ──▶ Redis (arq) ──▶ apps/worker ──┐
  form          │            │                                            │
  CSV replay    ┘            │ sync                                       │
                             ▼                                            ▼
                       agent/orchestrator (state machine) ──────▶ MCP client
                             │                                            │
                             │                              ┌─────────────┼─────────────┬─────────────┐
                             │                              ▼             ▼             ▼             ▼
                             │                        ticketing-mcp    kb-mcp     customer-mcp   policy-mcp
                             │                              │             │             │             │
                             ▼                              └─────────────┴──────┬──────┴─────────────┘
                      Postgres + pgvector  ◀─────────────────────────────────────┘
                             ▲
                             │
                       evals/runner  ◀── golden set JSONL ── configs/*.yaml
```

The agent is deliberately **stateless**: all state lives in Postgres and all side effects
go through a `ToolClient`. That makes a run replayable and the worker horizontally
scalable.

## Request lifecycle

1. **Ingest.** `POST /ingest` validates an `IngestRequest`, computes an idempotency key,
   and persists a `Ticket` (+ `TicketMessage`) inside one transaction.
   - *Sync* (default): the API immediately runs the orchestrator and writes the outcome back.
   - *Async* (`?async=true`): the ticket is persisted with `status="processing"` and
     enqueued. If enqueueing fails (Redis down) the API **fails open** and runs the agent
     inline — a ticket is never lost.
2. **Queue.** `apps/worker` claims the job and POSTs `{payload, run_sync: true}` to
   `POST /internal/worker`. The worker holds no DB pool; the API process owns the pool, so
   connection management stays in one place.
3. **Run.** `_process_ticket` re-checks idempotency. A ticket that is already *finished*
   is reported as-is; a ticket that is only *persisted* (`processing`) is **re-run** so
   the work is never silently dropped.
4. **Act.** The orchestrator plans actions; auto-resolve writes execute, risky writes are
   recorded as `PendingAction` rows for human approval via `POST /tickets/{id}/approve`.

### Idempotency & retries

- Idempotency key = hash of `(tenant_id, source, external_id)` or the content hash when no
  external id is present.
- arq jobs use a deterministic `_job_id` (`ticket:{tenant}:{hash}`) so a redelivery cannot
  double-process.
- `process_ticket` retries with backoff on transient errors and writes to a `DeadLetterJob`
  row when retries are exhausted (`max_tries=5`), so failures are visible, not lost.

## The agent state machine

Each stage is a pure-ish function with a **typed output schema** (`agent/schemas.py`).
Typing each stage is what lets the eval harness score them independently.

| # | Stage | Kind | Output | Notes |
|---|-------|------|--------|-------|
| 1 | Normalize | deterministic | `NormalizedTicket` | trims/quotes/signature stripping, language detect |
| 2 | PII redaction | deterministic | `RedactionResult` | redact before any model sees text |
| 3 | Classify | model | `Classification` | category, priority, sentiment, language, confidence |
| 4 | Dedupe | embeddings | `DedupeResult` | cosine vs. recent tickets |
| 5 | Retrieve | tool (kb-mcp) | `RetrievalResult` | hybrid BM25 + vector |
| — | Confidence gate | rule | — | `confidence < threshold` → escalate |
| 6 | Decide | model | `DecisionResult` | `auto_resolve` / `draft` / `escalate`, risk topics |
| 7 | Draft | model | `Draft` | only when a reply is warranted |
| 8 | Guardrails | deterministic | `GuardrailReport` | injection, PII leak, groundedness, tone, policy |
| 9 | Act | tools | `ActResult` | executed vs. pending (approval-gated) actions |

**Escalation rules** (fail closed):

- low classification *or* decision confidence;
- retrieval failure (kb-mcp unreachable);
- any guardrail failure;
- risk topic present (refund / account change / legal).

An escalated run performs **no writes**; risky actions become pending actions routed to
`escalations`, `refunds`, or `legal` queues.

## MCP tool layer

Four small servers, not one monolith, so each has a narrow blast radius, its own token, and
an independently versioned contract. Read tools are separated from write tools.

| Server | Read tools | Write tools |
|--------|-----------|-------------|
| ticketing-mcp | `get_ticket`, `list_similar_tickets` | `update_ticket`, `add_internal_note`, `assign_to_queue` |
| kb-mcp | `search_articles`, `get_article` | — |
| customer-mcp | `get_customer`, `get_order_history`, `get_plan_and_sla` | — |
| policy-mcp | `get_refund_policy`, `get_escalation_rules` | — |

The agent talks to these through the `ToolClient` protocol. Two implementations exist:
`InProcessToolClient` (direct calls; used by dev and the eval harness, fully offline) and
`MCPToolClient` (stdio / streamable-HTTP transport for deployed servers). Swapping them is
a config change, which is what lets the harness run with no network.

## Data model

| Table | Purpose |
|-------|---------|
| `tenants` | multi-tenancy root |
| `kb_articles` | knowledge base (+ pgvector embedding) |
| `policy_documents` | refund & escalation policy text |
| `tickets` | ticket header + current outcome (`status`, `decision`, `category`, …) |
| `ticket_messages` | raw/redacted message bodies |
| `agent_runs` | one row per orchestrator run (config snapshot, status) |
| `stage_results` | per-stage typed output, latency, input hash, error |
| `pending_actions` | approval-gated writes awaiting a human |
| `dead_letter_jobs` | jobs that exhausted retries |

Retrieval uses pgvector (HNSW index) with a JSONB fallback so the same code path works on
SQLite for hermetic tests.

## Configuration

All runtime config is one immutable `Settings` object (`agent/config.py`) read from the
environment / `.env`. Model routing is explicit: a cheap model for classification, a
stronger one for decide/draft/judge. Eval configs (`configs/*.yaml`) layer on top and are
snapshotted into each run for reproducibility.

## Observability

- `structlog` for structured logs.
- `agent_runs` / `stage_results` give a per-stage trace with latency and input hashes.
- The eval results DB (`evals_results.db` / Postgres) stores runs, metrics, and gate
  outcomes for `list-runs` and `diff`.

## Scaling & failure modes

- **Stateless workers** scale horizontally; the queue provides backpressure.
- **Rate limits** are handled by tenacity retries with backoff; exhausted jobs go to the DLQ.
- **Cost control** via model routing (cheap classify, stronger draft) and retrieval caching.
- **Graceful degradation**: dead Redis → inline fail-open; dead kb-mcp → escalate.
- **Load testing** with Locust (`make test` extras).
