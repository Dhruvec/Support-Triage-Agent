# ADR 0008 — Versioned golden set with a held-out split

**Status:** Accepted

## Context

Eval data decays: labels drift, slices fall out of date, and tuning against the same items
you report on inflates scores (eval leakage). Without a version stamp, a stored result
can't be tied to the data it scored.

## Decision

Store the golden set as versioned JSONL where **every record embeds `dataset_version`** and
a `slice`. Keep a held-out `test` split that is rarely touched, used only for honest
reporting, never for tuning. Configs pin `dataset_version`, and runs record it, so results
are comparable only within a version.

## Consequences

- Results are reproducible and comparable; a leaked item is visible as a version bump.
- Tuning stays honest because the reported split is protected.
- Relabelling requires a new version; diffs across versions are interpreted with care.

## Alternatives

- **Unversioned CSV** — cannot tell what a stored result scored.
- **One split for everything** — fast to overfit; scores stop meaning anything.
