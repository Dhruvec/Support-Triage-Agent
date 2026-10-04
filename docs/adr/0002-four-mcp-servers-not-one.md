# ADR 0002 — Four small MCP servers, not one monolith

**Status:** Accepted

## Context

The agent needs ticket, knowledge-base, customer, and policy capabilities. These could be
exposed as one MCP server with many tools, or split into several.

## Decision

Ship four servers (`mcp_servers/ticketing_server.py`, `kb_server.py`, `customer_server.py`,
`policy_server.py`), each with read tools separated from write tools and its own token.

## Consequences

- **Least privilege** — the kb/customer/policy servers have no write tools at all.
- **Narrow blast radius** — a bug or compromise in one server doesn't reach the others.
- **Independent versioning** — a KB tool contract can change without touching ticketing.
- **Clear ownership/tokens** — per-server `MCP_*_TOKEN`.
- More processes to run and monitor; the `MCPToolClient` adapter hides that from the agent.

## Alternatives

- **One MCP server** — simpler to run, but couples all capabilities and forces a single
  token and version for unrelated tools.
