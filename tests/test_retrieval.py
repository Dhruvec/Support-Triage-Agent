"""Unit tests for the hybrid (BM25 + vector) retriever."""

from __future__ import annotations

from agent.corpus import build_articles
from agent.retrieval import Article, HybridRetriever
from agent.schemas import RetrievalResult


def _articles() -> list[Article]:
    return [
        Article(
            article_id="kb-1",
            slug="reset-password",
            title="Reset your password",
            body="Go to sign in and choose forgot password.",
            tags=["account"],
        ),
        Article(
            article_id="kb-2",
            slug="refund-policy",
            title="Refund policy",
            body="Refunds are reviewed by a billing specialist.",
            tags=["refund"],
        ),
    ]


def test_search_returns_retrieval_result_ordered_desc():
    retriever = HybridRetriever(_articles())
    result = retriever.search("password reset", k=5)
    assert isinstance(result, RetrievalResult)
    assert result.query == "password reset"
    scores = [a.score for a in result.articles]
    assert scores == sorted(scores, reverse=True)
    assert result.articles[0].article_id == "kb-1"


def test_search_respects_k_and_caps_snippet():
    retriever = HybridRetriever(build_articles())
    result = retriever.search("refund", k=3)
    assert len(result.articles) <= 3
    for article in result.articles:
        assert len(article.snippet) <= 240


def test_search_empty_corpus_returns_empty_result():
    retriever = HybridRetriever([])
    result = retriever.search("anything")
    assert result.articles == []
    assert result.query == "anything"
