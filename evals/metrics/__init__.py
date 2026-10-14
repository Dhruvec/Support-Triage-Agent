"""Metric computations used by the eval runner.

Each submodule exposes a ``compute(...)`` function returning a dataclass with a
``to_dict()`` method, so the runner can aggregate results uniformly:

* :mod:`evals.metrics.classification` — accuracy, macro-F1, confusion matrix.
* :mod:`evals.metrics.retrieval` — recall@k, MRR, hit rate.
* :mod:`evals.metrics.decision` — escalation recall/precision, auto-resolve.
"""

from evals.metrics import classification, decision, retrieval

__all__ = ["classification", "decision", "retrieval"]
