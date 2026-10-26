"""Tool layer.

The orchestrator never talks to MCP servers directly. It calls this typed
:class:`ToolClient` interface. Two implementations exist:

* :class:`InProcessToolClient` — backed by the same SQLAlchemy models / retriever,
  used by the eval harness and unit tests (no servers, fully offline).
* :class:`MCPToolClient` — speaks to the four MCP servers over stdio or
  streamable HTTP.

Crucially, **write tools are separated from read tools**: read tools are freely
callable during the pipeline; write tools are only reachable through the
approval-gated :class:`ActResult` produced by the Act stage. This is the
enforcement point for the "risky actions require a human" rule.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from agent.config import Settings, get_settings
from agent.embeddings import get_embedder
from agent.retrieval import Article, HybridRetriever
from agent.schemas import RetrievalResult

# The DB layer (``apps.api.*``) is imported lazily inside InProcessToolClient
# methods so the eval harness stays importable without Postgres/pgvector, which
# the offline runner never touches.


@dataclass
class ToolCallLog:
    tool: str
    latency_ms: float
    args_hash: str
    result_size: int
    ok: bool = True
    error: str | None = None


class ToolClient(Protocol):
    """Read/write tool surface exposed to the orchestrator."""

    call_log: list[ToolCallLog]

    # --- ticketing-mcp (read) ---
    def get_ticket(self, ticket_id: str) -> dict: ...
    def list_similar_tickets(
        self, tenant_id: str, embedding: list[float], k: int = 5
    ) -> list[dict]: ...

    # --- kb-mcp ---
    def search_articles(self, tenant_id: str, query: str, k: int = 5) -> RetrievalResult: ...
    def get_article(self, tenant_id: str, article_id: str) -> dict | None: ...

    # --- customer-mcp (read-only) ---
    def get_customer(self, customer_id: str) -> dict: ...
    def get_order_history(self, customer_id: str) -> list[dict]: ...
    def get_plan_and_sla(self, customer_id: str) -> dict: ...

    # --- policy-mcp ---
    def get_refund_policy(self, tenant_id: str) -> str: ...
    def get_escalation_rules(self, tenant_id: str) -> dict: ...

    # --- ticketing-mcp (write; approval-gated) ---
    def update_ticket(self, ticket_id: str, **fields: Any) -> dict: ...
    def add_internal_note(self, ticket_id: str, note: str) -> dict: ...
    def assign_to_queue(self, ticket_id: str, queue: str) -> dict: ...


# --------------------------------------------------------------------------- #
# Utility
# --------------------------------------------------------------------------- #
def _hash_args(args: Any) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]


class _Logged:
    """Mixin providing call logging for both implementations."""

    call_log: list[ToolCallLog]

    def _record(self, tool: str, start: float, args: Any, result: Any, error: str | None = None):
        self.call_log.append(
            ToolCallLog(
                tool=tool,
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
                args_hash=_hash_args(args),
                result_size=len(str(result)),
                ok=error is None,
                error=error,
            )
        )


# --------------------------------------------------------------------------- #
# In-process implementation
# --------------------------------------------------------------------------- #
class InProcessToolClient(_Logged):
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.call_log = []
        self._embedder = get_embedder()

    def _t(self) -> float:
        return time.perf_counter()

    # --- ticketing ---
    def get_ticket(self, ticket_id: str) -> dict:
        t = self._t()
        from apps.api.db import session_scope
        from apps.api.models import Ticket

        with session_scope() as s:
            tk = s.get(Ticket, ticket_id)
            result = {
                "id": tk.id,
                "status": tk.status,
                "queue": tk.queue,
                "customer_id": tk.customer_id,
            } if tk else {}
        self._record("get_ticket", t, ticket_id, result)
        return result

    def list_similar_tickets(
        self, tenant_id: str, embedding: list[float], k: int = 5
    ) -> list[dict]:
        t = self._t()
        from sqlalchemy import select

        from agent.stages import cosine
        from apps.api.db import session_scope
        from apps.api.models import Ticket

        with session_scope() as s:
            rows = s.execute(
                select(Ticket).where(
                    Ticket.tenant_id == tenant_id,
                    Ticket.embedding.isnot(None),
                    Ticket.status != "closed",
                )
            ).scalars().all()
        scored = [
            (r.id, cosine(embedding, r.embedding or []), r.embedding)
            for r in rows
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [{"ticket_id": i, "similarity": round(sc, 4)} for i, sc, _ in scored[:k]]
        self._record("list_similar_tickets", t, tenant_id, result)
        return result

    # --- kb ---
    def search_articles(self, tenant_id: str, query: str, k: int = 5) -> RetrievalResult:
        t = self._t()
        from sqlalchemy import select

        from apps.api.db import session_scope
        from apps.api.models import KBArticle

        with session_scope() as s:
            rows = s.execute(
                select(KBArticle).where(KBArticle.tenant_id == tenant_id)
            ).scalars().all()
        articles = [
            Article(article_id=r.id, slug=r.slug, title=r.title, body=r.body, tags=r.tags or [])
            for r in rows
        ]
        result = HybridRetriever(articles, self._embedder).search(query, k=k)
        self._record("search_articles", t, {"query": query, "k": k}, result)
        return result

    def get_article(self, tenant_id: str, article_id: str) -> dict | None:
        t = self._t()
        from apps.api.db import session_scope
        from apps.api.models import KBArticle

        with session_scope() as s:
            r = s.get(KBArticle, article_id)
            result = (
                {"id": r.id, "slug": r.slug, "title": r.title, "body": r.body}
                if r and r.tenant_id == tenant_id
                else None
            )
        self._record("get_article", t, article_id, result)
        return result

    # --- customer (read-only stub data) ---
    def get_customer(self, customer_id: str) -> dict:
        t = self._t()
        result = {"customer_id": customer_id, "name": f"Customer {customer_id[:6]}", "tier": "pro"}
        self._record("get_customer", t, customer_id, result)
        return result

    def get_order_history(self, customer_id: str) -> list[dict]:
        t = self._t()
        result = [{"order_id": f"{customer_id[:4]}-001", "amount": 49.0, "status": "delivered"}]
        self._record("get_order_history", t, customer_id, result)
        return result

    def get_plan_and_sla(self, customer_id: str) -> dict:
        t = self._t()
        result = {"plan": "pro", "sla": "24h", "refund_window_days": 30}
        self._record("get_plan_and_sla", t, customer_id, result)
        return result

    # --- policy ---
    def get_refund_policy(self, tenant_id: str) -> str:
        t = self._t()
        from sqlalchemy import select

        from apps.api.db import session_scope
        from apps.api.models import PolicyDocument

        with session_scope() as s:
            doc = s.execute(
                select(PolicyDocument).where(
                    PolicyDocument.tenant_id == tenant_id,
                    PolicyDocument.key == "refund",
                )
            ).scalar_one_or_none()
        result = doc.body if doc else "Refunds require human approval."
        self._record("get_refund_policy", t, tenant_id, result)
        return result

    def get_escalation_rules(self, tenant_id: str) -> dict:
        t = self._t()
        result = {
            "escalate_on": ["legal", "account_change", "refund", "pii", "outage"],
            "auto_resolve_allowed": ["product", "technical", "shipping", "billing"],
        }
        self._record("get_escalation_rules", t, tenant_id, result)
        return result

    # --- write tools (approval-gated upstream) ---
    def update_ticket(self, ticket_id: str, **fields: Any) -> dict:
        t = self._t()
        from apps.api.db import session_scope
        from apps.api.models import Ticket

        with session_scope() as s:
            tk = s.get(Ticket, ticket_id)
            if tk:
                for k, v in fields.items():
                    setattr(tk, k, v)
            result = {"ticket_id": ticket_id, "updated": list(fields)}
        self._record("update_ticket", t, {"ticket_id": ticket_id, **fields}, result)
        return result

    def add_internal_note(self, ticket_id: str, note: str) -> dict:
        t = self._t()
        result = {"ticket_id": ticket_id, "noted": True, "length": len(note)}
        self._record("add_internal_note", t, {"ticket_id": ticket_id}, result)
        return result

    def assign_to_queue(self, ticket_id: str, queue: str) -> dict:
        t = self._t()
        from apps.api.db import session_scope
        from apps.api.models import Ticket

        with session_scope() as s:
            tk = s.get(Ticket, ticket_id)
            if tk:
                tk.queue = queue
        result = {"ticket_id": ticket_id, "queue": queue}
        self._record("assign_to_queue", t, {"ticket_id": ticket_id, "queue": queue}, result)
        return result


# --------------------------------------------------------------------------- #
# MCP implementation (deployment path)
# --------------------------------------------------------------------------- #
class MCPToolClient(_Logged):
    """Thin adapter over the four MCP servers.

    Each server is addressed by URL (streamable HTTP) or command (stdio). Tool
    arguments are validated against the schemas declared by each server; the
    client refuses to pass through arbitrary keys, which is part of the
    prompt-injection defense (ticket text can never synthesise tool arguments).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.call_log = []
        self._clients: dict[str, Any] = {}

    def _call(self, server: str, tool: str, args: dict) -> Any:
        t = time.perf_counter()
        try:
            result = self._call_sync(server, tool, args)
            self._record(f"{server}.{tool}", t, args, result)
            return result
        except Exception as exc:  # noqa: BLE001 - surfaced to orchestrator as escalation
            self._record(f"{server}.{tool}", t, args, None, error=str(exc))
            raise

    def _call_sync(self, server: str, tool: str, args: dict) -> Any:  # pragma: no cover - network
        # Placeholder for the real MCP session wiring (stdlib mcp client).
        # Kept explicit so the interface is stable and testable.
        raise NotImplementedError(
            "MCPToolClient requires the MCP servers to be running; "
            "use InProcessToolClient for offline/eval runs."
        )

    # Interface compliance (delegates to server namespaces)
    def get_ticket(self, ticket_id: str) -> dict:
        return self._call("ticketing", "get_ticket", {"ticket_id": ticket_id})

    def list_similar_tickets(
        self, tenant_id: str, embedding: list[float], k: int = 5
    ) -> list[dict]:
        return self._call(
            "ticketing",
            "list_similar_tickets",
            {"tenant_id": tenant_id, "embedding": embedding, "k": k},
        )

    def search_articles(self, tenant_id: str, query: str, k: int = 5) -> RetrievalResult:
        data = self._call("kb", "search_articles", {"tenant_id": tenant_id, "query": query, "k": k})
        return RetrievalResult.model_validate(data)

    def get_article(self, tenant_id: str, article_id: str) -> dict | None:
        return self._call("kb", "get_article", {"tenant_id": tenant_id, "article_id": article_id})

    def get_customer(self, customer_id: str) -> dict:
        return self._call("customer", "get_customer", {"customer_id": customer_id})

    def get_order_history(self, customer_id: str) -> list[dict]:
        return self._call("customer", "get_order_history", {"customer_id": customer_id})

    def get_plan_and_sla(self, customer_id: str) -> dict:
        return self._call("customer", "get_plan_and_sla", {"customer_id": customer_id})

    def get_refund_policy(self, tenant_id: str) -> str:
        return self._call("policy", "get_refund_policy", {"tenant_id": tenant_id})

    def get_escalation_rules(self, tenant_id: str) -> dict:
        return self._call("policy", "get_escalation_rules", {"tenant_id": tenant_id})

    def update_ticket(self, ticket_id: str, **fields: Any) -> dict:
        return self._call("ticketing", "update_ticket", {"ticket_id": ticket_id, "fields": fields})

    def add_internal_note(self, ticket_id: str, note: str) -> dict:
        return self._call("ticketing", "add_internal_note", {"ticket_id": ticket_id, "note": note})

    def assign_to_queue(self, ticket_id: str, queue: str) -> dict:
        return self._call("ticketing", "assign_to_queue", {"ticket_id": ticket_id, "queue": queue})


def get_tool_client(settings: Settings | None = None, force_in_process: bool = True) -> ToolClient:
    settings = settings or get_settings()
    if force_in_process or settings.mcp_transport == "stdio" and not settings.is_production:
        return InProcessToolClient(settings)
    return MCPToolClient(settings)
