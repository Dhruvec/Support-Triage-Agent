"""Retrieval metrics: recall@k, MRR, hit rate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalMetrics:
    recall_at_k: float = 0.0
    mrr: float = 0.0
    hit_rate: float = 0.0
    support: int = 0

    def to_dict(self) -> dict:
        return {
            "recall_at_k": round(self.recall_at_k, 4),
            "mrr": round(self.mrr, 4),
            "hit_rate": round(self.hit_rate, 4),
            "support": self.support,
        }


def compute(retrieved: list[list[str]], relevant: list[set[str]]) -> RetrievalMetrics:
    """retrieved[i] is the ranked list of article ids for item i.

    relevant[i] is the set of gold relevant article ids. Recall@k = fraction of
    gold articles found in the top k; MRR uses the rank of the first relevant
    hit; hit rate = fraction of items with at least one relevant hit.
    """

    assert len(retrieved) == len(relevant)
    n = len(retrieved)
    if n == 0:
        return RetrievalMetrics()

    recalls: list[float] = []
    rrs: list[float] = []
    hits = 0
    for ranked, gold in zip(retrieved, relevant, strict=False):
        if not gold:
            continue
        found = set(ranked) & gold
        recalls.append(len(found) / len(gold))
        rr = 0.0
        for rank, doc in enumerate(ranked, start=1):
            if doc in gold:
                rr = 1.0 / rank
                break
        rrs.append(rr)
        if found:
            hits += 1

    denom = len(recalls) or 1
    return RetrievalMetrics(
        recall_at_k=sum(recalls) / denom,
        mrr=sum(rrs) / denom,
        hit_rate=hits / denom,
        support=len(recalls),
    )
