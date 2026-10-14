"""Dataset loading and split handling.

Datasets live as versioned JSONL under ``evals/datasets``. Splits are an
explicit field so the harness can enforce the "held-out test" discipline that
prevents eval leakage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"

Split = Literal["train", "dev", "test"]


@dataclass
class Expected:
    category: str | None = None
    priority: str | None = None
    sentiment: str | None = None
    decision: str | None = None
    escalate: bool | None = None
    grounded: bool | None = None
    duplicate_of: str | None = None
    injection: bool | None = None
    pii: bool | None = None
    article_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Expected":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class EvalItem:
    id: str
    dataset_version: str
    slice: str
    subject: str
    body: str
    expected: Expected
    split: Split = "dev"
    notes: str = ""

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()


def load_items(path: str | Path | None = None, version: str = "v0") -> list[EvalItem]:
    if path is None:
        matches = sorted(DATASETS_DIR.glob(f"golden_{version}.jsonl"))
        if not matches:
            raise FileNotFoundError(f"no dataset for version {version}")
        path = matches[0]
    path = Path(path)
    items: list[EvalItem] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            items.append(
                EvalItem(
                    id=rec["id"],
                    dataset_version=rec.get("dataset_version", version),
                    slice=rec.get("slice", "unknown"),
                    subject=rec.get("subject", ""),
                    body=rec.get("body", ""),
                    expected=Expected.from_dict(rec.get("expected", {})),
                    split=rec.get("split", "dev"),
                    notes=rec.get("notes", ""),
                )
            )
    return items


def splits(items: list[EvalItem]) -> dict[str, list[EvalItem]]:
    out: dict[str, list[EvalItem]] = {"train": [], "dev": [], "test": []}
    for it in items:
        out.setdefault(it.split, []).append(it)
    return out


def filter_slices(items: list[EvalItem], slices: list[str] | None) -> list[EvalItem]:
    if not slices:
        return items
    return [it for it in items if it.slice in slices]


def iter_jsonl(items: list[EvalItem]) -> Iterator[dict]:
    for it in items:
        yield {
            "id": it.id,
            "dataset_version": it.dataset_version,
            "slice": it.slice,
            "subject": it.subject,
            "body": it.body,
            "split": it.split,
            "expected": it.expected.__dict__,
            "notes": it.notes,
        }
