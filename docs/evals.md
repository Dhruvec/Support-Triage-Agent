# Eval harness

The harness answers one question repeatedly: *did this change make the agent better or
worse?* It scores each pipeline layer on a versioned golden set, driven by a config file,
and turns the result into CI gates that block a merge on regression.

## Why per-layer metrics

A single end-to-end pass/fail hides *where* quality moved. Because every stage emits a
typed result, a regression can be localized: a drop in `classification_accuracy` with flat
`escalation_recall` points at the classifier, not the decision policy.

## Metrics

### Classification

- accuracy, per-class precision/recall/F1, confusion matrix
- per-slice accuracy (e.g. is multilingual worse than happy path?)

Source: [`evals/metrics/classification.py`](../evals/metrics/classification.py).

### Decision

- **escalation recall** — of the tickets that *should* escalate, how many did. This is the
  headline metric: a missed escalation is the costly error.
- **auto-resolve precision** — of the tickets auto-resolved, how many were safe to
  auto-resolve. A false auto-resolve is the second costly error.
- decision accuracy (3-way: auto_resolve / draft / escalate)

Source: [`evals/metrics/decision.py`](../evals/metrics/decision.py).

### Retrieval

- recall@k, MRR, hit rate against the gold article set for each item.

Source: [`evals/metrics/retrieval.py`](../evals/metrics/retrieval.py).

### Reply quality (LLM-as-judge)

A rubric judge scores each draft on weighted dimensions and yields a pass/fail at a
threshold. To keep the judge honest it is:

- pinned to a **specific judge model** and a **rubric version** (config),
- **calibrated** against a small human-labeled set with Cohen's kappa, and
- usable in **pairwise** mode with position swapping for A/B prompt comparisons.

Sources: [`evals/judges/rubric.py`](../evals/judges/rubric.py),
[`evals/judges/calibration.py`](../evals/judges/calibration.py),
[`evals/judges/pairwise.py`](../evals/judges/pairwise.py).

### Safety & ops

- guardrail pass rates (injection, PII leak, groundedness, tone, policy)
- adversarial slices: injection, PII bait, out-of-policy refund, no-KB, legal
- ops: cost, latency, tokens, error rate

## Datasets

Versioned JSONL in [`evals/datasets/`](../evals/datasets). Each record carries
`dataset_version` and a `slice`. A held-out `test` split is kept out of day-to-day runs to
avoid eval leakage. See [`evals/datasets/README.md`](../evals/datasets/README.md) for the
field reference and the 12 slices.

## Suites

Defined as data in [`evals/suites.py`](../evals/suites.py) so the CLI, the runner, and CI
all agree on their meaning:

| suite | slices | question |
|-------|--------|----------|
| `smoke` | happy_path, angry, vague | fast must-pass on every PR |
| `regression` | all | quality drift over time |
| `adversarial` | injection, PII, refund, no_kb, legal | safe under attack |

## Configs

A run is fully described by a YAML config ([`evals/config.py`](../evals/config.py)):

- `dataset_version`, `pipeline` (prompt version, retrieval_k, dedupe threshold, stub flags),
  `models` (per-stage routing), `judge` (model / heuristic mode), and `gates` (thresholds).

Two are checked in:

- [`configs/v1.yaml`](../configs/v1.yaml) — targets real models; the release gate.
- [`configs/ci.yaml`](../configs/ci.yaml) — **hermetic**: forces the deterministic stub LLM
  and heuristic judge so CI needs no API key or network.

Each run records a config snapshot, so a stored result traces back to exact settings.

## Gates & CI

`evals.cli run` evaluates the config's `gates` against the metrics and **exits non-zero on
failure**. [`.github/workflows/evals.yml`](../.github/workflows/evals.yml) runs smoke →
adversarial → regression on every push and PR (each as its own step, so a failure names
the gate that broke) and uploads the results DB as an artifact.

```bash
make eval-ci    # reproduce the CI gates locally
```

## Baseline (offline, stub LLM + heuristic judge, golden set v0)

These are the numbers `configs/ci.yaml` floors are calibrated just below. They are a
*floor, not a target*: they catch a regression (a dropped escalation, a broken classifier,
a crash) while tolerating a few relabelled items.

| suite | items | escalation recall | auto-resolve precision | decision acc | classification acc | judge pass |
|-------|:-----:|:-----------------:|:----------------------:|:------------:|:------------------:|:----------:|
| smoke | 21 | 0.889 | 1.000 | 0.571 | 0.857 | 1.000 |
| adversarial | 11 | 1.000 | — | 1.000 | 0.545 | — |
| regression | 45 | 0.964 | — | 0.689 | 0.667 | — |

Cost, tokens-paid, and error rate are all zero on the stub, as expected. The real-model
configs (`configs/v1.yaml`) sit above these on the same data.

## Run-to-run diffs

Results persist to the eval DB. Compare runs to see metric deltas, gate flips, and the
specific items that changed verdict:

```bash
python -m evals.cli list-runs
python -m evals.cli diff <run_id> <prev_run_id>
```

## Adding items

1. Append a JSONL line to `evals/datasets/golden_v0.jsonl` with `dataset_version`, `slice`,
   `subject`/`body`, and the `expected.*` labels (see the dataset README).
2. Pick the slice so the item lands in the right suite(s).
3. Run `make eval-ci`; if a gate legitimately moves, update the floor in `configs/ci.yaml`
   in the same PR and say why in the description.

Keep the held-out `test` split out of tuning so the numbers stay honest.
