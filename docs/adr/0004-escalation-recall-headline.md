# ADR 0004 — Escalation recall as the headline decision metric

**Status:** Accepted

## Context

Decision errors are not symmetric. Auto-resolving something that needed a human (a
refund, a legal threat, an angry VIP) is a costly, sometimes irreversible error. Drafting
when we could have auto-resolved is a small inefficiency.

## Decision

Make **escalation recall** — the share of tickets that should escalate and did — the
headline decision metric, with the highest gate threshold. Track **auto-resolve
precision** (of what we auto-resolved, how much was safe) as the second guard. Optimize
accuracy only within those constraints.

## Consequences

- The system is tuned to fail toward a human, not toward silent auto-resolution.
- A regression that drops a single escalation can fail CI even if accuracy improves.
- Slightly more human load by design; that is the correct trade.

## Alternatives

- **Optimize overall accuracy** — treats a missed escalation and a missed auto-resolve as
  equal cost, which they are not.
