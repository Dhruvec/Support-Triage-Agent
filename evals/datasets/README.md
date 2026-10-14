# Datasets

Versioned JSONL datasets. Each line is one labeled ticket. `dataset_version` is
embedded in every record so a run can pin exactly which data it scored.

## Fields

| field | description |
|-------|-------------|
| `id` | stable item id (used for run-to-run diffs / flipped-ticket analysis) |
| `dataset_version` | dataset version tag, e.g. `v0` |
| `slice` | one of the slices below |
| `subject`, `body` | the ticket as received |
| `expected.category` | gold category label |
| `expected.priority` | gold priority label |
| `expected.sentiment` | gold sentiment label |
| `expected.decision` | gold decision (`auto_resolve` / `draft` / `escalate`) |
| `expected.escalate` | boolean; the costly-error label (escalation recall) |
| `expected.grounded` | whether a grounded answer is expected (draft quality) |
| `expected.duplicate_of` | optional; id of the original ticket for dedupe evals |
| `expected.injection` | optional; true for adversarial injection items |
| `expected.pii` | optional; true for PII-bait items |
| `notes` | human note (why this label) |

## Slices

- `happy_path` — clear, low-risk, KB-covered
- `angry` — negative/angry sentiment
- `multilingual` — non-English tickets
- `vague` — underspecified; must escalate
- `multi_issue` — more than one problem
- `duplicate` — near-duplicate of another item
- `outage` — outage / severe spikes
- `legal` — legal or compliance topics
- `adversarial_injection` — prompt injection attempts
- `adversarial_pii` — PII bait / account-change requests
- `adversarial_refund` — out-of-policy refund requests
- `adversarial_no_kb` — no answer exists in the KB

## Growth plan

`golden_v0.jsonl` is the seed set. The plan is 300–500 items: fold in
real-style public support data (check licenses) plus generated-and-hand-verified
synthetic tickets, keeping a held-out `test` split that is rarely touched to
avoid eval leakage.
