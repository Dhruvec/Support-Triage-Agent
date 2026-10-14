"""customer-mcp: read-only customer context (CRM / orders).

Deliberately read-only: no tool here can mutate customer or order state. This
keeps the blast radius small and makes the "separate read from write" rule
structural rather than a convention.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import check_token

mcp = FastMCP("customer-mcp")

# In production these read from the CRM/orders DB. Kept deterministic here so
# the agent and its evals are reproducible.
_CUSTOMERS = {
    "cust-001": {"name": "Ada Lovelace", "email_domain": "example.com", "tier": "pro"},
    "cust-002": {"name": "Grace Hopper", "email_domain": "example.org", "tier": "enterprise"},
    "cust-003": {"name": "Alan Turing", "email_domain": "example.net", "tier": "starter"},
    "cust-004": {"name": "Katherine Johnson", "email_domain": "example.com", "tier": "pro"},
}


@mcp.tool()
def get_customer(customer_id: str, token: str | None = None) -> dict:
    check_token(token, "MCP_CUSTOMER_TOKEN")
    data = _CUSTOMERS.get(customer_id, {"name": "Unknown", "tier": "starter"})
    return {"customer_id": customer_id, **data}


@mcp.tool()
def get_order_history(customer_id: str, token: str | None = None) -> list[dict]:
    check_token(token, "MCP_CUSTOMER_TOKEN")
    tier = _CUSTOMERS.get(customer_id, {}).get("tier", "starter")
    return [
        {"order_id": f"{customer_id}-001", "amount": 49.0, "status": "delivered", "plan": "monthly"},
        {"order_id": f"{customer_id}-002", "amount": 490.0, "status": "active", "plan": "annual"},
    ][: 2 if tier != "starter" else 1]


@mcp.tool()
def get_plan_and_sla(customer_id: str, token: str | None = None) -> dict:
    check_token(token, "MCP_CUSTOMER_TOKEN")
    tier = _CUSTOMERS.get(customer_id, {}).get("tier", "starter")
    sla = {"starter": "72h", "pro": "24h", "enterprise": "4h"}.get(tier, "72h")
    return {
        "customer_id": customer_id,
        "plan": tier,
        "sla": sla,
        "refund_window_days": 30 if tier != "starter" else 7,
    }


def main() -> None:  # pragma: no cover - server entry
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":  # pragma: no cover
    main()
