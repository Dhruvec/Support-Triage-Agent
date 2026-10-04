"""Seed data: default tenant, KB articles, policy docs, and sample tickets.

Run with ``python -m apps.api.seed`` (or ``make seed``). Idempotent: re-running
updates existing rows by natural key rather than duplicating them.
"""

from __future__ import annotations

from sqlalchemy import select

from agent.corpus import KB_ARTICLES, POLICIES, TENANT_ID
from agent.embeddings import get_embedder
from apps.api.db import init_db, session_scope
from apps.api.models import KBArticle, PolicyDocument, Tenant, Ticket, TicketMessage

SAMPLE_TICKETS = [
    {
        "subject": "Cannot log in after password reset",
        "body": (
            "Hi, I reset my password but the link says expired. I never got a new email. "
            "Can you help me log in? Thanks."
        ),
        "customer_id": "cust-001",
    },
    {
        "subject": "Charged twice this month",
        "body": (
            "I was charged twice on my invoice for the same month. Please fix this, "
            "I want a refund for the duplicate charge. This is unacceptable."
        ),
        "customer_id": "cust-002",
    },
    {
        "subject": "Where is my package?",
        "body": "My tracking has not updated in 3 days. Standard shipping. Order ORD-7781.",
        "customer_id": "cust-003",
    },
    {
        "subject": "Does Pro include SSO?",
        "body": "Quick question: does the Pro plan include SSO or is that Enterprise only? Thanks!",
        "customer_id": "cust-004",
    },
]


def seed() -> None:
    init_db()
    embedder = get_embedder()
    with session_scope() as s:
        if s.get(Tenant, TENANT_ID) is None:
            s.add(Tenant(id=TENANT_ID, name="Acme Support"))

        for a in KB_ARTICLES:
            existing = s.execute(
                select(KBArticle).where(
                    KBArticle.tenant_id == TENANT_ID, KBArticle.slug == a["slug"]
                )
            ).scalar_one_or_none()
            text_val = f"{a['title']} {a['body']} {' '.join(a['tags'])}"
            emb = embedder.embed(text_val)
            if existing:
                existing.title = a["title"]
                existing.body = a["body"]
                existing.tags = a["tags"]
                existing.embedding = emb
            else:
                s.add(
                    KBArticle(
                        tenant_id=TENANT_ID, slug=a["slug"], title=a["title"],
                        body=a["body"], tags=a["tags"], embedding=emb,
                    )
                )

        for p in POLICIES:
            existing = s.execute(
                select(PolicyDocument).where(
                    PolicyDocument.tenant_id == TENANT_ID, PolicyDocument.key == p["key"]
                )
            ).scalar_one_or_none()
            if existing:
                existing.title = p["title"]
                existing.body = p["body"]
            else:
                s.add(
                    PolicyDocument(
                        tenant_id=TENANT_ID, key=p["key"], title=p["title"], body=p["body"]
                    )
                )

        for i, t in enumerate(SAMPLE_TICKETS):
            idem = f"seed-{i}"
            existing = s.execute(
                select(Ticket).where(
                    Ticket.tenant_id == TENANT_ID, Ticket.idempotency_key == idem
                )
            ).scalar_one_or_none()
            if existing:
                continue
            tk = Ticket(
                tenant_id=TENANT_ID, idempotency_key=idem, source="seed",
                subject=t["subject"], customer_id=t["customer_id"], status="new",
                embedding=embedder.embed(t["body"]),
            )
            s.add(tk)
            s.flush()
            s.add(TicketMessage(ticket_id=tk.id, role="customer", body_raw=t["body"]))


def main() -> None:  # pragma: no cover - CLI entry
    seed()
    print("Seed complete.")


if __name__ == "__main__":  # pragma: no cover
    main()
