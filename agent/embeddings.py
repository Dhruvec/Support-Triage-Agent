"""Embeddings.

In production this delegates to an embedding API. For the offline harness (and
for deterministic dedupe/retrieval evals) we ship a hashing embedder: tokens are
hashed into fixed buckets with L2 normalization. It is not semantic, but it is
deterministic, dependency-free, and makes similarity well-defined for tests,
CI, and stub-mode runs.
"""

from __future__ import annotations

import hashlib
import math
import re

from agent.config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder."""

    name = "hashing"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or get_settings().embedding_dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall(text.lower())
        if not tokens:
            return vec
        for tok in tokens:
            h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            vec = [v / norm for v in vec]
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class OpenAIEmbedder:
    """Real embedder (optional). Falls back only when explicitly selected."""

    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or get_settings().embedding_model

    def embed(self, text: str) -> list[float]:  # pragma: no cover - network
        import httpx

        settings = get_settings()
        resp = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.anthropic_api_key}"},
            json={"model": self.model, "input": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def get_embedder() -> HashingEmbedder:
    """Return the process embedder. Defaults to deterministic hashing."""

    return HashingEmbedder()
