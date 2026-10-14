"""Unit tests for the deterministic hashing embedder."""

from __future__ import annotations

import math

import pytest

from agent.embeddings import HashingEmbedder, get_embedder


def test_embedding_dimension_and_determinism():
    embedder = HashingEmbedder(dim=32)
    first = embedder.embed("hello world")
    second = embedder.embed("hello world")
    assert len(first) == 32
    assert first == second


def test_embedding_is_l2_normalized():
    embedder = HashingEmbedder(dim=64)
    vec = embedder.embed("support ticket about billing")
    norm = math.sqrt(sum(v * v for v in vec))
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_embedding_empty_text_is_zero_vector():
    embedder = HashingEmbedder(dim=8)
    assert embedder.embed("") == [0.0] * 8


def test_embed_batch_matches_individual():
    embedder = HashingEmbedder(dim=16)
    batch = embedder.embed_batch(["a b", "c d"])
    assert batch == [embedder.embed("a b"), embedder.embed("c d")]


def test_get_embedder_default_is_hashing():
    embedder = get_embedder()
    assert isinstance(embedder, HashingEmbedder)
