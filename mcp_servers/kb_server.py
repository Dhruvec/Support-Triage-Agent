"""kb-mcp: knowledge-base hybrid search.

Read-only. Exposes ``search_articles`` and ``get_article`` as tools, and KB
articles as MCP resources (``kb://article/{id}``) so clients can browse them.

Run: ``python -m mcp_servers.kb_server`` (stdio) or with MCP_TRANSPORT=streamable-http.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from agent.embeddings import get_embedder
from agent.retrieval import Article, HybridRetriever
from apps.api.db import session_scope
from apps.api.models import KBArticle
from mcp_servers.common import check_token, jsonable

mcp = FastMCP("kb-mcp")


def _load_articles(tenant_id: str) -> list[tuple[KBArticle, Article]]:
    with session_scope() as s:
        rows = s.execute(select(KBArticle).where(KBArticle.tenant_id == tenant_id)).scalars().all()
        return [
            (
                r,
                Article(
                    article_id=r.id, slug=r.slug, title=r.title, body=r.body, tags=r.tags or []
                ),
            )
            for r in rows
        ]


@mcp.tool()
def search_articles(tenant_id: str, query: str, k: int = 5, token: str | None = None) -> dict:
    """Hybrid (BM25 + vector) KB search. Returns ranked articles with snippets."""

    check_token(token, "MCP_KB_TOKEN")
    pairs = _load_articles(tenant_id)
    if not pairs:
        return {"articles": [], "query": query, "bm25_hits": 0, "vector_hits": 0}
    retriever = HybridRetriever([a for _, a in pairs], get_embedder())
    result = retriever.search(query, k=k)
    return jsonable(result.model_dump())


@mcp.tool()
def get_article(tenant_id: str, article_id: str, token: str | None = None) -> dict | None:
    """Fetch a single KB article by id."""

    check_token(token, "MCP_KB_TOKEN")
    with session_scope() as s:
        r = s.get(KBArticle, article_id)
        if r is None or r.tenant_id != tenant_id:
            return None
        return jsonable({"id": r.id, "slug": r.slug, "title": r.title, "body": r.body})


@mcp.resource("kb://article/{article_id}")
def article_resource(article_id: str) -> str:
    """Expose a KB article as a resource (tenant-agnostic id lookup)."""

    with session_scope() as s:
        r = s.get(KBArticle, article_id)
        if r is None:
            return "not found"
        return f"# {r.title}\n\n{r.body}"


def main() -> None:  # pragma: no cover - server entry
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":  # pragma: no cover
    main()
