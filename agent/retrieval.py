"""Hybrid retrieval (BM25 + vector) with a light rerank.

This mirrors what ``kb-mcp.search_articles`` does server-side, and doubles as the
in-process retriever used by the eval harness when MCP servers aren't running.
Keeping one implementation means retrieval evals measure the same algorithm the
agent uses in production.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from agent.embeddings import HashingEmbedder
from agent.schemas import RetrievedArticle, RetrievalResult

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class Article:
    article_id: str
    slug: str
    title: str
    body: str
    tags: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.title} {self.body} {' '.join(self.tags)}"


class HybridRetriever:
    def __init__(self, articles: list[Article], embedder: HashingEmbedder | None = None) -> None:
        self.articles = articles
        self.embedder = embedder or HashingEmbedder()
        self._doc_tokens = [_tokens(a.text) for a in articles]
        self._doc_vecs = [self.embedder.embed(a.text) for a in articles]
        self._avg_len = (
            sum(len(d) for d in self._doc_tokens) / len(self._doc_tokens)
            if self._doc_tokens
            else 1.0
        )
        self._df: Counter = Counter()
        for toks in self._doc_tokens:
            self._df.update(set(toks))

    # --- BM25 ---
    def _bm25(self, query: list[str], idx: int, k1: float = 1.5, b: float = 0.75) -> float:
        doc = self._doc_tokens[idx]
        tf = Counter(doc)
        score = 0.0
        n = len(self._doc_tokens)
        for term in query:
            if term not in tf:
                continue
            df = self._df.get(term, 0) or 1
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            freq = tf[term]
            denom = freq + k1 * (1 - b + b * (len(doc) / self._avg_len))
            score += idf * (freq * (k1 + 1)) / denom
        return score

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=False))

    def search(self, query: str, k: int = 5) -> RetrievalResult:
        if not self.articles:
            return RetrievalResult(query=query)

        q_tokens = _tokens(query)
        q_vec = self.embedder.embed(query)

        bm25 = [self._bm25(q_tokens, i) for i in range(len(self.articles))]
        vec = [self._cosine(q_vec, v) for v in self._doc_vecs]

        def norm(scores: list[float]) -> list[float]:
            lo, hi = min(scores), max(scores)
            if hi - lo < 1e-9:
                return [0.0 for _ in scores]
            return [(s - lo) / (hi - lo) for s in scores]

        bm25_n, vec_n = norm(bm25), norm(vec)
        combined = [0.5 * bm25_n[i] + 0.5 * vec_n[i] for i in range(len(self.articles))]

        bm25_hits = sum(1 for s in bm25 if s > 0)
        vector_hits = sum(1 for s in vec if s > 0)

        order = sorted(range(len(self.articles)), key=lambda i: combined[i], reverse=True)[:k]
        results = []
        for i in order:
            a = self.articles[i]
            snippet = a.body[:240]
            results.append(
                RetrievedArticle(
                    article_id=a.article_id,
                    slug=a.slug,
                    title=a.title,
                    score=round(combined[i], 4),
                    snippet=snippet,
                )
            )
        return RetrievalResult(
            articles=results,
            query=query,
            bm25_hits=bm25_hits,
            vector_hits=vector_hits,
        )
