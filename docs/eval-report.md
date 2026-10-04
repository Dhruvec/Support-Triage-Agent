# Eval report

A concrete, reproducible snapshot of the harness output on the checked-in golden set.
This is the artefact a reviewer reads to answer *"is the agent good, and how do we know?"*
It accompanies [`docs/evals.md`](evals.md) (which defines the metrics) and the ADRs.

| | |
|---|---|
| **Date** | 2026-10-04 |
| **Dataset** | `evals/datasets/golden_v0.jsonl` (`dataset_version: v0`) |
| **Config** | [`configs/ci.yaml`](../configs/ci.yaml) — hermetic |
| **LLM** | deterministic stub (`force_stub_llm: true`) |
| **Judge** | heuristic rubric-lite (`judge.force_heuristic: true`) |
| **Cost / network** | \$0.00, none |

> These are **offline, stub-LLM** numbers. They exist to make CI hermetic and to catch a
> *regression* (a dropped escalation, a broken classifier, a crash) — they are a **floor,
> not a quality target**. The real-model runs (`configs/v1.yaml` with an API key) are the
> release gate; the gates in `ci.yaml` are set just below the floors below.

---

## Summary

| suite | items | escalation recall | auto-resolve precision | decision acc | category acc | judge pass | gates |
|-------|:-----:|:-----------------:|:----------------------:|:------------:|:------------:|:----------:|:-----:|
| `smoke` | 21 | 0.889 | 1.000 | 0.571 | 0.857 | 1.000 | ✅ pass |
| `adversarial` | 11 | 1.000 | 1.000 | 1.000 | 0.545 | 1.000 | ✅ pass |
| `regression` | 45 | 0.964 | 1.000 | 0.689 | 0.667 | 1.000 | ✅ pass |

All three suites pass every gate. The headline metric — **escalation recall** — holds at
**0.96** across the full set, and the adversarial suite is perfect (nothing slipped past the
guardrails).

---

## Gate results

Thresholds from `configs/ci.yaml`; values are the `regression` (full-set) run.

| gate | threshold | regression value | smoke value | result |
|------|:---------:|:----------------:|:-----------:|:------:|
| `escalation_recall` | ≥ 0.85 | 0.964 | 0.889 | ✅ |
| `auto_resolve_precision` | ≥ 0.95 | 1.000 | 1.000 | ✅ |
| `decision_accuracy` | ≥ 0.55 | 0.689 | 0.571 | ✅ |
| `classification_accuracy` | ≥ 0.50 | 0.667 | 0.857 | ✅ |
| `judge_pass_rate` | ≥ 0.90 | 1.000 | 1.000 | ✅ |
| `max_cost_usd` | ≤ 5.0 | 0.000 | 0.000 | ✅ |
| `max_error_rate` | ≤ 0.05 | 0.000 | 0.000 | ✅ |

`classification_accuracy` is the **category** accuracy (the primary classification task).
Priority and sentiment are scored and reported but not gated.

---

## Where the signal is good

- **Escalation recall** — 0.964 on 45 items (28 escalations). Only one item that should
  have escalated did not (`g0020`). Since a missed escalation is the costly error, this is
  exactly the behaviour the design optimises for.
- **Auto-resolve precision** — 1.000. Every ticket the agent chose to auto-resolve was safe
  to auto-resolve; no false "we solved it" claims.
- **Adversarial safety** — injection, PII-bait, out-of-policy refund, no-KB and legal
  slices all pass (decision accuracy 1.000). The fail-closed guardrail stage escalates
  correctly under attack.
- **Cost and errors** — zero cost, zero error rate, ~12 ms/item on the stub.

## Where the signal is weak (known, and expected on the stub)

- **Priority accuracy** (0.489 regression, 0.091 adversarial) is low. Priority is
  inherently fuzzy and the deterministic stub uses a keyword heuristic; this is the least
  trustworthy number here. It is **not gated**, precisely because the stub cannot speak to
  it. Real-model runs should be used before drawing priority conclusions.
- **Auto-resolve recall = 0.0** (smoke + regression). The stub is deliberately
  conservative and escalates rather than auto-resolves, so there were no true positives to
  recall. Precision of 1.0 on a tiny positive count is weak evidence; treat it as
  "no false auto-resolves observed", not "auto-resolve works well".
- **False escalations** — 13/45 in regression (recall 0.964 with precision 0.675). The
  stub over-escalates on items it labels as risky. That trades precision for recall, which
  is the intended direction, but a real model should tighten it.
- **Retrieval is unscored** — `evals/datasets/golden_v0.jsonl` carries **no gold
  `article_ids`**, so `retrieval.support == 0` for every suite and recall@k / MRR / hit rate
  are not exercised. This is the biggest gap in the current eval; populating gold article
  ids is the highest-value dataset addition.

---

## Per-suite detail

### `smoke` — 21 items (happy_path, angry, vague)

| metric | value |
|--------|:-----:|
| escalation recall | 0.889 |
| escalation precision | 0.500 |
| auto-resolve precision | 1.000 |
| decision accuracy | 0.571 |
| category accuracy | 0.857 |
| sentiment accuracy | 0.905 |
| priority accuracy | 0.571 |
| judge pass rate | 1.000 (9 judged) |
| tokens | 4,681 |
| avg latency | 13.4 ms |
| error rate | 0.000 |

Missed escalation: `g0020`. False escalations: `g0001`, `g0004`, `g0006`, `g0007`,
`g0008`, `g0010`, `g0042`, `g0043`.

### `adversarial` — 11 items (injection, PII, refund, no_kb, legal)

| metric | value |
|--------|:-----:|
| escalation recall | 1.000 |
| escalation precision | 1.000 |
| auto-resolve precision | 1.000 |
| decision accuracy | 1.000 |
| category accuracy | 0.545 |
| sentiment accuracy | 0.818 |
| priority accuracy | 0.091 |
| judge pass rate | 1.000 (4 judged) |
| tokens | 2,320 |
| avg latency | 12.6 ms |
| error rate | 0.000 |

No missed or false escalations. Every adversarial item is contained by the guardrails.

### `regression` — 45 items (all slices)

| metric | value |
|--------|:-----:|
| escalation recall | 0.964 |
| escalation precision | 0.675 |
| auto-resolve precision | 1.000 |
| decision accuracy | 0.689 |
| category accuracy | 0.667 |
| sentiment accuracy | 0.778 |
| priority accuracy | 0.489 |
| judge pass rate | 1.000 (15 judged) |
| tokens | 8,977 |
| avg latency | 11.8 ms |
| error rate | 0.000 |

Missed escalation: `g0020`. False escalations: `g0001`, `g0004`, `g0006`, `g0007`,
`g0008`, `g0010`, `g0015`, `g0016`, `g0018`, `g0026`, `g0027`, `g0042`, `g0043`.

---

## Reproduce

```bash
# all three suites, hermetic (no API key, no network) — the CI gate
make eval-ci

# one suite, machine-readable
python -m evals.cli run --suite regression --config configs/ci.yaml --no-persist --json

# persist and compare against a previous run
python -m evals.cli run --suite regression --config configs/v1.yaml --persist
python -m evals.cli list-runs
python -m evals.cli diff last prev
```

## Reading this report honestly

- The numbers describe the **stub**, not a production model. They prove the *harness* and
  the *guardrails* work end-to-end; they do not prove reply quality.
- Retrieval is not yet scored (no gold article ids), so the "R" in RAG is unmeasured here.
- A gate passing means "no regression against a floor", not "good". Tighten the floors in
  `configs/ci.yaml` in the same PR whenever a real-model run raises them.

---

## Complete metric scores (every metric, every suite)

The full metric payload produced by the runner after the evaluation phase. Latencies vary
between runs (host load); all other values are deterministic on the stub.

### `smoke` — 21 items

**Classification**

| task | accuracy | macro-F1 | support |
|------|:--------:|:--------:|:-------:|
| category | 0.8571 | 0.8641 | 21 |
| priority | 0.5714 | 0.2452 | 21 |
| sentiment | 0.9048 | 0.6444 | 21 |

Per-class (precision / recall / F1 / support):

| task | class | precision | recall | F1 | support |
|------|-------|:---------:|:------:|:--:|:-------:|
| category | account | 1.0000 | 0.8000 | 0.8889 | 5 |
| category | billing | 1.0000 | 1.0000 | 1.0000 | 2 |
| category | cancellation | 1.0000 | 1.0000 | 1.0000 | 2 |
| category | other | 0.6667 | 0.6667 | 0.6667 | 3 |
| category | product | 0.7500 | 1.0000 | 0.8571 | 3 |
| category | refund | 1.0000 | 1.0000 | 1.0000 | 2 |
| category | shipping | 1.0000 | 1.0000 | 1.0000 | 2 |
| category | technical | 0.5000 | 0.5000 | 0.5000 | 2 |
| priority | p0 | 0.0000 | 0.0000 | 0.0000 | 1 |
| priority | p1 | 0.2500 | 0.2000 | 0.2222 | 5 |
| priority | p2 | 0.6471 | 0.9167 | 0.7586 | 12 |
| priority | p3 | 0.0000 | 0.0000 | 0.0000 | 3 |
| sentiment | angry | 1.0000 | 1.0000 | 1.0000 | 5 |
| sentiment | negative | 0.0000 | 0.0000 | 0.0000 | 0 |
| sentiment | neutral | 1.0000 | 0.8750 | 0.9333 | 16 |

Confusion (gold → predicted, non-zero cells only):

- **category**: account→{account 4, product 1} · billing→{billing 2} · cancellation→{cancellation 2} · other→{other 2, technical 1} · product→{product 3} · refund→{refund 2} · shipping→{shipping 2} · technical→{other 1, technical 1}
- **priority**: p0→{p1 1} · p1→{p1 1, p2 4} · p2→{p1 1, p2 11} · p3→{p1 1, p2 2}
- **sentiment**: angry→{angry 5} · neutral→{negative 2, neutral 14}

**Decision**

| metric | score |
|--------|:-----:|
| escalation_recall | 0.8889 |
| escalation_precision | 0.5000 |
| auto_resolve_precision | 1.0000 |
| auto_resolve_recall | 0.0000 |
| accuracy | 0.5714 |
| support | 21 |
| missed_escalations | `g0020` |
| false_escalations | `g0001, g0004, g0006, g0007, g0008, g0010, g0042, g0043` |

**Retrieval** — `support: 0`, note: `no gold article_ids` (recall@k / MRR / hit rate not exercised).

**Judge** — `pass_rate: 1.000`, `num_judged: 9`, `mode: heuristic`.

**Ops** — `total_tokens: 4681`, `total_cost_usd: 0.0`, `avg_latency_ms: 13.39`, `error_rate: 0.0`, `pass_rate: 0.2381`.

**Gates** — escalation_recall 0.8889 (≥0.85) ✅ · auto_resolve_precision 1.0 (≥0.95) ✅ · decision_accuracy 0.5714 (≥0.55) ✅ · classification_accuracy 0.8571 (≥0.50) ✅ · judge_pass_rate 1.0 (≥0.90) ✅ · max_cost_usd 0.0 (≤5.0) ✅ · max_error_rate 0.0 (≤0.05) ✅ → **passed: true**.

### `adversarial` — 11 items

**Classification**

| task | accuracy | macro-F1 | support |
|------|:--------:|:--------:|:-------:|
| category | 0.5455 | 0.4214 | 11 |
| priority | 0.0909 | 0.0556 | 11 |
| sentiment | 0.8182 | 0.3000 | 11 |

Per-class (precision / recall / F1 / support):

| task | class | precision | recall | F1 | support |
|------|-------|:---------:|:------:|:--:|:-------:|
| category | account | 0.5000 | 0.3333 | 0.4000 | 3 |
| category | other | 0.2000 | 0.5000 | 0.2857 | 2 |
| category | product | 0.0000 | 0.0000 | 0.0000 | 2 |
| category | refund | 1.0000 | 1.0000 | 1.0000 | 4 |
| priority | p0 | 0.0000 | 0.0000 | 0.0000 | 1 |
| priority | p1 | 0.0000 | 0.0000 | 0.0000 | 9 |
| priority | p2 | 0.0909 | 1.0000 | 0.1667 | 1 |
| sentiment | angry | 0.0000 | 0.0000 | 0.0000 | 1 |
| sentiment | negative | 0.0000 | 0.0000 | 0.0000 | 0 |
| sentiment | neutral | 0.9000 | 0.9000 | 0.9000 | 10 |

Confusion (gold → predicted, non-zero cells only):

- **category**: account→{account 1, other 2} · other→{account 1, other 1} · product→{other 2} · refund→{refund 4}
- **priority**: p0→{p2 1} · p1→{p2 9} · p2→{p2 1}
- **sentiment**: angry→{neutral 1} · neutral→{negative 1, neutral 9}

**Decision**

| metric | score |
|--------|:-----:|
| escalation_recall | 1.0000 |
| escalation_precision | 1.0000 |
| auto_resolve_precision | 1.0000 |
| auto_resolve_recall | 1.0000 |
| accuracy | 1.0000 |
| support | 11 |
| missed_escalations | — |
| false_escalations | — |

**Retrieval** — `support: 0`, note: `no gold article_ids`.

**Judge** — `pass_rate: 1.000`, `num_judged: 4`, `mode: heuristic`.

**Ops** — `total_tokens: 2320`, `total_cost_usd: 0.0`, `avg_latency_ms: 30.61`, `error_rate: 0.0`, `pass_rate: 0.2727`.

**Gates** — escalation_recall 1.0 ✅ · auto_resolve_precision 1.0 ✅ · decision_accuracy 1.0 ✅ · classification_accuracy 0.5455 ✅ · judge_pass_rate 1.0 ✅ · max_cost_usd 0.0 ✅ · max_error_rate 0.0 ✅ → **passed: true**.

### `regression` — 45 items

**Classification**

| task | accuracy | macro-F1 | support |
|------|:--------:|:--------:|:-------:|
| category | 0.6667 | 0.7164 | 45 |
| priority | 0.4889 | 0.3681 | 45 |
| sentiment | 0.7778 | 0.6480 | 45 |

Per-class (precision / recall / F1 / support):

| task | class | precision | recall | F1 | support |
|------|-------|:---------:|:------:|:--:|:-------:|
| category | account | 0.8571 | 0.5000 | 0.6316 | 12 |
| category | billing | 0.6000 | 0.7500 | 0.6667 | 4 |
| category | cancellation | 1.0000 | 1.0000 | 1.0000 | 3 |
| category | other | 0.2308 | 0.6000 | 0.3333 | 5 |
| category | product | 0.7500 | 0.6000 | 0.6667 | 5 |
| category | refund | 1.0000 | 0.8750 | 0.9333 | 8 |
| category | shipping | 1.0000 | 1.0000 | 1.0000 | 3 |
| category | technical | 0.6667 | 0.4000 | 0.5000 | 5 |
| priority | p0 | 1.0000 | 0.6000 | 0.7500 | 5 |
| priority | p1 | 0.2500 | 0.0556 | 0.0909 | 18 |
| priority | p2 | 0.4737 | 0.9474 | 0.6316 | 19 |
| priority | p3 | 0.0000 | 0.0000 | 0.0000 | 3 |
| sentiment | angry | 1.0000 | 0.8333 | 0.9091 | 6 |
| sentiment | negative | 0.1667 | 0.2000 | 0.1818 | 5 |
| sentiment | neutral | 0.8529 | 0.8529 | 0.8529 | 34 |

Confusion (gold → predicted, non-zero cells only):

- **category**: account→{account 6, billing 2, other 3, product 1} · billing→{billing 3, other 1} · cancellation→{cancellation 3} · other→{account 1, other 3, technical 1} · product→{other 2, product 3} · refund→{other 1, refund 7} · shipping→{shipping 3} · technical→{other 3, technical 2}
- **priority**: p0→{p0 3, p1 1, p2 1} · p1→{p1 1, p2 17} · p2→{p1 1, p2 18} · p3→{p1 1, p2 2}
- **sentiment**: angry→{angry 5, neutral 1} · negative→{negative 1, neutral 4} · neutral→{negative 5, neutral 29}

**Decision**

| metric | score |
|--------|:-----:|
| escalation_recall | 0.9643 |
| escalation_precision | 0.6750 |
| auto_resolve_precision | 1.0000 |
| auto_resolve_recall | 0.0000 |
| accuracy | 0.6889 |
| support | 45 |
| missed_escalations | `g0020` |
| false_escalations | `g0001, g0004, g0006, g0007, g0008, g0010, g0015, g0016, g0018, g0026, g0027, g0042, g0043` |

**Retrieval** — `support: 0`, note: `no gold article_ids`.

**Judge** — `pass_rate: 1.000`, `num_judged: 15`, `mode: heuristic`.

**Ops** — `total_tokens: 8977`, `total_cost_usd: 0.0`, `avg_latency_ms: 28.33`, `error_rate: 0.0`, `pass_rate: 0.2444`.

**Gates** — escalation_recall 0.9643 (≥0.85) ✅ · auto_resolve_precision 1.0 (≥0.95) ✅ · decision_accuracy 0.6889 (≥0.55) ✅ · classification_accuracy 0.6667 (≥0.50) ✅ · judge_pass_rate 1.0 (≥0.90) ✅ · max_cost_usd 0.0 (≤5.0) ✅ · max_error_rate 0.0 (≤0.05) ✅ → **passed: true**.

### Metrics not yet scored

| metric | status | reason |
|--------|:------:|--------|
| retrieval.recall_at_k | not scored | no gold `article_ids` in the v0 golden set |
| retrieval.mrr | not scored | no gold `article_ids` |
| retrieval.hit_rate | not scored | no gold `article_ids` |
| judge.llm_pass_rate | not scored | `force_heuristic: true` (heuristic judge only) |
| ops.cost (real models) | 0.0 | stub LLM, no paid calls |
