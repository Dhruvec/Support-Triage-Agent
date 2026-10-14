"""Shared knowledge-base and policy corpus.

This module is the single source of truth for the seeded KB articles and policy
documents. It is imported by:

* :mod:`apps.api.seed` — which persists them to the relational store, and
* :mod:`evals.offline_client` — which serves them fully in-process so the eval
  harness runs with no Postgres/network.

Keeping the text here (rather than duplicated in the seeder and the harness)
means retrieval evals measure *exactly* the corpus the agent sees in production.
"""

from __future__ import annotations

import hashlib

from agent.retrieval import Article

# The default tenant used by the seeder and offline harness runs.
TENANT_ID = "default"


KB_ARTICLES = [
    {
        "slug": "reset-password",
        "title": "How to reset your password",
        "body": (
            "Go to the sign-in page, choose 'Forgot password', enter your email, and "
            "follow the link sent to your inbox. Links expire after 30 minutes. If you "
            "do not receive the email, check spam or contact support."
        ),
        "tags": ["account", "password", "login"],
    },
    {
        "slug": "billing-cycle",
        "title": "Understanding your billing cycle and invoices",
        "body": (
            "Subscriptions renew on the same day each month. Invoices are emailed and "
            "available under Billing > Invoices. If you were charged twice, we will "
            "investigate and correct the duplicate charge after verification."
        ),
        "tags": ["billing", "invoice", "charge"],
    },
    {
        "slug": "refund-policy",
        "title": "Refund policy",
        "body": (
            "Refunds are available within 30 days of purchase for annual plans. Pro-rated "
            "refunds are not automatic: refund requests are reviewed by a billing "
            "specialist. Submit your order id and reason; a specialist will follow up."
        ),
        "tags": ["refund", "billing", "policy"],
    },
    {
        "slug": "shipping-times",
        "title": "Shipping and delivery times",
        "body": (
            "Standard shipping takes 3-5 business days. Tracking numbers are emailed when "
            "the package leaves the warehouse. If tracking has not updated in 48 hours, "
            "contact support so we can open a carrier trace."
        ),
        "tags": ["shipping", "delivery", "tracking"],
    },
    {
        "slug": "cancel-subscription",
        "title": "How to cancel your subscription",
        "body": (
            "You can cancel anytime under Account > Subscription > Cancel. Access continues "
            "until the end of the current period. Cancellation does not delete data; you can "
            "export it first under Account > Data."
        ),
        "tags": ["cancellation", "account", "subscription"],
    },
    {
        "slug": "two-factor-auth",
        "title": "Setting up two-factor authentication",
        "body": (
            "Enable 2FA under Account > Security. Scan the QR code with an authenticator app "
            "and store the backup codes. If you lose your device, use a backup code or contact "
            "support with proof of ownership."
        ),
        "tags": ["account", "security", "2fa"],
    },
    {
        "slug": "api-rate-limits",
        "title": "API rate limits and 429 errors",
        "body": (
            "The API allows 600 requests per minute per key. A 429 response means you have "
            "exceeded the limit; back off exponentially and retry. Limits scale with your plan."
        ),
        "tags": ["technical", "api", "errors"],
    },
    {
        "slug": "plan-comparison",
        "title": "Comparing plans and features",
        "body": (
            "Starter includes core features for individuals. Pro adds higher limits, priority "
            "support with a 24h SLA, and team seats. Enterprise adds SSO, audit logs, and a "
            "custom SLA. Upgrade or downgrade under Account > Subscription."
        ),
        "tags": ["product", "plans", "pricing"],
    },
]


POLICIES = [
    {
        "key": "refund",
        "title": "Refund policy",
        "body": (
            "Refunds are reviewed case by case by a billing specialist. Agents must never "
            "issue or promise a refund without human approval. Eligible windows: 30 days for "
            "annual plans, 7 days for monthly plans."
        ),
    },
    {
        "key": "escalation",
        "title": "Escalation rules",
        "body": (
            "Escalate immediately for: legal threats, account changes, refunds, PII concerns, "
            "and service outages. Low model confidence also requires escalation."
        ),
    },
]


def kb_article_id(slug: str) -> str:
    """Deterministic article id for a slug.

    The seeder uses random UUIDs at insert time; the offline harness cannot know
    them, so it derives a *stable* id from the slug. Retrieval gold labels in the
    golden dataset therefore cannot reference raw UUIDs — they reference slugs —
    and both the harness and any dataset tooling use this function to normalise.
    """

    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:12]
    return f"kb-{digest}"


def build_articles(embedder=None) -> list[Article]:
    """Return the KB corpus as retriever-ready :class:`Article` objects."""

    return [
        Article(
            article_id=kb_article_id(a["slug"]),
            slug=a["slug"],
            title=a["title"],
            body=a["body"],
            tags=list(a["tags"]),
        )
        for a in KB_ARTICLES
    ]


def policy_map() -> dict[str, dict]:
    return {p["key"]: p for p in POLICIES}
