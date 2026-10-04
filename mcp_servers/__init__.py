"""Four small MCP servers: ticketing, kb, customer, policy.

Design: one server per bounded context rather than a single monolith. Each
server is small, independently testable (contract tests / MCP Inspector), and
has its own token. Read tools are separated from write tools.
"""
