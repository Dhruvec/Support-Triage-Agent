"""ticketing-mcp: ticket read/write.

Read tools: ``get_ticket``, ``list_similar_tickets``.
Write tools: ``update_ticket``, ``add_internal_note``, ``assign_to_queue``.

Write tools are only reachable through the orchestrator's approval-gated Act
stage; the server still enforces the token so a compromised agent cannot call
writes directly.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from apps.api.db import session_scope
from apps.api.models import Ticket, TicketMessage
from mcp_servers.common import check_token, jsonable

mcp = FastMCP("ticketing-mcp")


# ----------------------------- read tools ---------------------------------- #
@mcp.tool()
def get_ticket(ticket_id: str, token: str | None = None) -> dict:
    check_token(token, "MCP_TICKETING_TOKEN")
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return {}
        return jsonable(
            {
                "id": t.id,
                "tenant_id": t.tenant_id,
                "subject": t.subject,
                "status": t.status,
                "queue": t.queue,
                "customer_id": t.customer_id,
                "messages": [m.body_raw for m in t.messages],
            }
        )


@mcp.tool()
def list_similar_tickets(tenant_id: str, ticket_id: str, k: int = 5, token: str | None = None) -> list[dict]:
    """Return open tickets most similar to the given ticket (dedupe support)."""

    check_token(token, "MCP_TICKETING_TOKEN")
    from agent.stages import cosine

    with session_scope() as s:
        base = s.get(Ticket, ticket_id)
        if base is None or base.embedding is None:
            return []
        rows = s.execute(
            select(Ticket).where(
                Ticket.tenant_id == tenant_id,
                Ticket.id != ticket_id,
                Ticket.embedding.isnot(None),
                Ticket.status != "closed",
            )
        ).scalars().all()
        scored = [(r.id, cosine(base.embedding, r.embedding or [])) for r in rows]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [{"ticket_id": i, "similarity": round(s_, 4)} for i, s_ in scored[:k]]


# ----------------------------- write tools --------------------------------- #
@mcp.tool()
def update_ticket(ticket_id: str, fields: dict, token: str | None = None) -> dict:
    check_token(token, "MCP_TICKETING_TOKEN")
    allowed = {"status", "queue", "category", "priority", "sentiment", "decision"}
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return {"ok": False, "error": "not found"}
        for k, v in fields.items():
            if k in allowed:
                setattr(t, k, v)
    return {"ok": True, "ticket_id": ticket_id, "updated": list(fields)}


@mcp.tool()
def add_internal_note(ticket_id: str, note: str, token: str | None = None) -> dict:
    check_token(token, "MCP_TICKETING_TOKEN")
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return {"ok": False, "error": "not found"}
        s.add(
            TicketMessage(
                ticket_id=ticket_id, role="internal", body_raw=note, is_internal_note=True
            )
        )
    return {"ok": True, "ticket_id": ticket_id}


@mcp.tool()
def assign_to_queue(ticket_id: str, queue: str, token: str | None = None) -> dict:
    check_token(token, "MCP_TICKETING_TOKEN")
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        if t is None:
            return {"ok": False, "error": "not found"}
        t.queue = queue
    return {"ok": True, "ticket_id": ticket_id, "queue": queue}


def main() -> None:  # pragma: no cover - server entry
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":  # pragma: no cover
    main()
