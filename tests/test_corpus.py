"""Unit tests for the shared KB/policy corpus."""

from __future__ import annotations

from agent import corpus
from agent.retrieval import Article


def test_kb_article_id_is_deterministic_and_prefixed():
    first = corpus.kb_article_id("reset-password")
    second = corpus.kb_article_id("reset-password")
    assert first == second
    assert first.startswith("kb-")
    assert len(first) == len("kb-") + 12
    assert corpus.kb_article_id("a") != corpus.kb_article_id("b")


def test_corpus_constants():
    assert corpus.TENANT_ID == "default"
    assert len(corpus.KB_ARTICLES) == 8
    assert len(corpus.POLICIES) == 2


def test_build_articles_shape():
    articles = corpus.build_articles()
    assert len(articles) == len(corpus.KB_ARTICLES)
    for article in articles:
        assert isinstance(article, Article)
        assert article.article_id == corpus.kb_article_id(article.slug)
        assert article.title
        assert article.body
        assert isinstance(article.tags, list)


def test_policy_map_keys():
    pmap = corpus.policy_map()
    assert set(pmap) == {"refund", "escalation"}
    assert pmap["refund"]["title"] == "Refund policy"
