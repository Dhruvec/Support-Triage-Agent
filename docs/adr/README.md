# Architecture decision records

Short, dated records of the decisions that shaped this system, kept so future changes can
see *why* something is the way it is. Format: Context → Decision → Consequences →
Alternatives.

| ADR | Decision |
|-----|----------|
| [0001](0001-explicit-state-machine-not-one-prompt.md) | Explicit state-machine pipeline instead of one giant prompt |
| [0002](0002-four-mcp-servers-not-one.md) | Four small MCP servers, not one monolith |
| [0003](0003-eval-harness-first.md) | Build the eval harness before the rest of the agent |
| [0004](0004-escalation-recall-headline.md) | Escalation recall as the headline decision metric |
| [0005](0005-fail-open-ingest-fail-closed-agent.md) | Fail-open ingest, fail-closed agent |
| [0006](0006-hermetic-ci-evals.md) | Hermetic offline evals in CI |
| [0007](0007-stateless-worker-ships-jobs-to-api.md) | Stateless worker ships jobs to the API runner |
| [0008](0008-versioned-golden-set-and-held-out-split.md) | Versioned golden set with a held-out split |
