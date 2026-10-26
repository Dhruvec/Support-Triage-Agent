"""Offline tool client for the eval harness.

The production :class:`agent.tools.InProcessToolClient` is backed by Postgres,
and :class:`agent.tools.MCPToolClient` needs the four MCP servers running. The
harness must run with neither, in CI, so this module provides a third
implementation of the :class:`agent.tools.ToolClient` protocol that serves the
exact production corpus straight from :mod:`agent.corpus`.

It faithfully mirrors ``InProcessToolClient``:

* ``search_articles`` uses the same :class:`agent.retrieval.HybridRetriever`, so
  retrieval evals measure the production algorithm and corpus.
* tickets are held in memory so the dedupe stage has something to compare
  against; the runner registers each golden item as a prior ticket, which makes
  the ``duplicate`` slice meaningful without a database.
* every call is recorded into ``call_log`` for tool-use metrics.
"""

from __future__ import annotations

import time
from typing import Any

from agent.config import Settings, get_settings
from agent.corpus import TENANT_ID, build_articles, policy_map
from agent.embeddings import get_embedder
from agent.retrieval import Article, HybridRetriever
from agent.schemas import RetrievalResult
from agent.tools import ToolCallLog, _Logged


class OfflineToolClient(_Logged):
    """In-memory :class:`ToolClient` for offline / CI eval runs."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        articles: list[Article] | None = None,
        tenant_id: str = TENANT_ID,
    ) -> None:
        self.settings = settings or get_settings()
        self.tenant_id = tenant_id
        self.call_log: list[ToolCallLog] = []
        self._embedder = get_embedder()
        self._articles = articles if articles is not None else build_articles()
        self._retriever = HybridRetriever(self._articles, self._embedder)
        self._policies = policy_map()
        self._tickets: dict[str, dict] = {}
        self._notes: list[dict] = []
        self._updates: list[dict] = []
        self._queues: dict[str, str] = {}

    def _t(self) -> float:
        return time.perf_counter()

    # -- harness helpers -------------------------------------------------- #
    def register_ticket(self, ticket_id: str, body: str, **meta: Any) -> None:
        """Seed a ticket with its embedding so dedupe can find it later."""

        self._tickets[ticket_id] = {
            "id": ticket_id,
            "embedding": self._embedder.embed(body),
            "status": meta.pop("status", "open"),
            "queue": meta.pop("queue", "inbox"),
            "customer_id": meta.pop("customer_id", None),
            **meta,
        }

    @property
    def executed_writes(self) -> list[dict]:
        """Writes the orchestrator actually performed (for safety evals)."""

        out = [{"tool": "update_ticket", **u} for u in self._updates]
        out += [
            {"tool": "assign_to_queue", "ticket_id": tid, "queue": q}
            for tid, q in self._queues.items()
        ]
        return out

    # --- ticketing-mcp (read) ------------------------------------------- #
    def get_ticket(self, ticket_id: str) -> dict:
        t = self._t()
        result = dict(self._tickets.get(ticket_id, {}))
        self._record("get_ticket", t, ticket_id, result)
        return result

    def list_similar_tickets(
        self, tenant_id: str, embedding: list[float], k: int = 5
    ) -> list[dict]:
        t = self._t()
        from agent.stages import cosine

        scored = [
            (tid, cosine(embedding, tk["embedding"]))
            for tid, tk in self._tickets.items()
            if tk.get("embedding") and tk.get("status") != "closed"
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [{"ticket_id": i, "similarity": round(sc, 4)} for i, sc in scored[:k]]
        self._record("list_similar_tickets", t, tenant_id, result)
        return result

    # --- kb-mcp --------------------------------------------------------- #
    def search_articles(self, tenant_id: str, query: str, k: int = 5) -> RetrievalResult:
        t = self._t()
        result = self._retriever.search(query, k=k)
        self._record("search_articles", t, {"query": query, "k": k}, result)
        return result

    def get_article(self, tenant_id: str, article_id: str) -> dict | None:
        t = self._t()
        result = None
        for a in self._articles:
            if a.article_id == article_id:
                result = {"id": a.article_id, "slug": a.slug, "title": a.title, "body": a.body}
                break
        self._record("get_article", t, article_id, result)
        return result

    # --- customer-mcp (read-only) --------------------------------------- #
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

    # --- policy-mcp ----------------------------------------------------- #
    def get_refund_policy(self, tenant_id: str) -> str:
        t = self._t()
        doc = self._policies.get("refund", {})
        result = doc.get("body", "Refunds require human approval.")
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

    # --- ticketing-mcp (write; approval-gated upstream) ----------------- #
    def update_ticket(self, ticket_id: str, **fields: Any) -> dict:
        t = self._t()
        if ticket_id in self._tickets:
            self._tickets[ticket_id].update(fields)
        self._updates.append({"ticket_id": ticket_id, **fields})
        result = {"ticket_id": ticket_id, "updated": list(fields)}
        self._record("update_ticket", t, {"ticket_id": ticket_id, **fields}, result)
        return result

    def add_internal_note(self, ticket_id: str, note: str) -> dict:
        t = self._t()
        self._notes.append({"ticket_id": ticket_id, "length": len(note)})
        result = {"ticket_id": ticket_id, "noted": True, "length": len(note)}
        self._record("add_internal_note", t, {"ticket_id": ticket_id}, result)
        return result

    def assign_to_queue(self, ticket_id: str, queue: str) -> dict:
        t = self._t()
        self._queues[ticket_id] = queue
        if ticket_id in self._tickets:
            self._tickets[ticket_id]["queue"] = queue
        result = {"ticket_id": ticket_id, "queue": queue}
        self._record("assign_to_queue", t, {"ticket_id": ticket_id, "queue": queue}, result)
        return result
