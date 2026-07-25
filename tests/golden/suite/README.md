# Golden semantic query suite (§29)

**Owner:** Member 4 — Data and Science.

This directory holds the ten required **golden end-to-end queries** (§29), one
JSON per case, validated against `data/schemas/golden_case.schema.json`.

> Not to be confused with the sibling **integration video fixture**
> (`../golden_synthetic.mp4`), which is a deterministic engineering fixture for
> ingestion/coverage/restart/export checks and is explicitly **not** a semantic
> benchmark. As that fixture's own README states, semantic and verifier truth
> comes from this independently controlled Member-4 suite and the held-out pools —
> never from the geometric-marker video.

| # | case | exercises | expected headline |
|---|---|---|---|
| 1 | `golden_01_object` | simple object | VERIFIED MATCH |
| 2 | `golden_02_attribute` | attribute | VERIFIED MATCH |
| 3 | `golden_03_action` | action | VERIFIED MATCH |
| 4 | `golden_04_binding` | actor–object binding | VERIFIED MATCH |
| 5 | `golden_05_cross_window` | cross-window before/after order | VERIFIED MATCH |
| 6 | `golden_06_absent` | absent / no verified match | NO VERIFIED MATCH |
| 7 | `golden_07_unobservable` | insufficient visual evidence | INSUFFICIENT VISUAL EVIDENCE |
| 8 | `golden_08_disjunction` | bounded `OR` | VERIFIED MATCH |
| 9 | `golden_09_visible_absence` | visible absence — **assessable + occluded** variants | NO VERIFIED MATCH / INSUFFICIENT |
| 10 | `golden_10_bounded_count` | bounded count with a **fragmentation decoy** (→ `unresolved`) | VERIFIED MATCH |

Every case is **synthetic** (`synthetic: true`) and asserts the expected
deterministic headline (§4.2) and archive conclusion (§4) structurally — no
invented model scores. When authorized footage exists, these families are
re-pointed at real sealed sessions and re-annotated from the human ledger; the
structure does not change.

Run the structural test:

```bash
python tests/golden/test_golden_structure.py -v
```

**Release rule (§29):** run the golden suite **cache-bypassed** before every
release candidate.
