# RAZIEL evaluation report — TEMPLATE

**Project:** RAZIEL — Temporal Evidence Intelligence. Retrieval subsystem: *Eyes of God*.
**Owner:** Member 4 — Data and Science.
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §22.

> **Fill rule.** Every numeric cell starts as `not_yet_measured`. Replace a cell **only**
> with a value produced by `python -m eval.run_eval --predictions <bundle.json> ...` on the
> declared held-out split. Never hand-enter a number. Optional lanes (spatial, track/graph,
> bounded-logic) stay `not_yet_measured` until real held-out results for that lane exist.
> Copy this file to `eval/reports/evaluation_report_<date>.md` before filling it.

---

## 0. Run identity

| Field | Value |
|---|---|
| Evaluation date | `not_yet_measured` |
| Eval config hash | `not_yet_measured` |
| System operating-point hash | `not_yet_measured` |
| Dataset revision (manifest hashes) | `not_yet_measured` |
| Split evaluated | `test` (held-out) |
| t-IoU threshold | 0.5 |
| Declared budget | candidate ≤ 32 clusters / verification ≤ 24 clusters / ≤ 60 s |
| Predictions produced by | `not_yet_measured` |

---

## 1. Primary dashboard (§22.1)

### 1.1 Candidate recall
| Metric | B1 | B2 | B3 | FULL | FULL+GRAPH | FULL+STR |
|---|---|---|---|---|---|---|
| Interval candidate recall | — | — | — | — | — | — |
| Complete-set positive-query recall | — | — | — | — | — | — |
| Action/order subset recall | — | — | — | — | — | — |
| Recall within verification budget | — | — | — | — | — | — |

### 1.2 Temporal set (one-to-one, t-IoU = 0.5)
| Metric | FULL | FULL+GRAPH | FULL+STR |
|---|---|---|---|
| Precision | — | — | — |
| Recall | — | — | — |
| F1 | — | — | — |

### 1.3 Empty-set rejection F1
| Config | F1 | reviewed absent families |
|---|---|---|
| FULL | — | — |

### 1.4 Required-condition semantic macro-F1
Ground-truth classes: supported / contradicted / unobservable. `undetermined` predictions
count as unresolved/incorrect and are reported separately by reason.

| Metric | Value |
|---|---|
| Macro-F1 | — |
| Per-class F1 (supported / contradicted / unobservable) | — / — / — |
| System undetermined rate | — |
| Undetermined by reason | — |

Confusion matrix (supported/contradicted/unobservable): _paste from run._

### 1.5 Boundary error (seconds)
| Metric | Value |
|---|---|
| Median absolute start error | — |
| Median absolute end error | — |
| Median combined error | — |

### 1.6 Latency and efficiency
| Metric | median | p95 |
|---|---|---|
| Retrieval | — | — |
| Verification | — | — |
| Uncached end-to-end | — | — |

| Metric | Value |
|---|---|
| VLM calls / query | — |
| Candidates or clusters / query | — |
| Indexing throughput (ticks/s) | — |

*(p95 requires ≥20 varied warm runs; median ≥10 warm runs; result caches bypassed — §9.3.)*

### 1.7 Coverage
| Metric | Value |
|---|---|
| Sampled-tick coverage | — |
| Assembly completeness | — |
| Verification completeness | — |

### 1.8 Spatial evidence quality — *only if enabled* (`not_yet_measured` otherwise)
| Metric | Value |
|---|---|
| Evidence-box validity | — |
| Overlay failure rate | — |

### 1.9 Track and graph quality — *only if enabled*
| Metric | Value |
|---|---|
| Track fragmentation rate | — |
| Duplicate-track rate | — |
| Count absolute error | — |
| Graph-pattern candidate recall | — |
| Edge-evidence validity | — |

### 1.10 Bounded-logic correctness — *only if enabled*
| Metric | Value |
|---|---|
| Exact outcome by operator (`any` / `visible_none` / `count`) | — / — / — |
| Unobservable/undetermined rate under occlusion & fragmentation | — |
| **False clean-negatives from missing coverage (MUST be 0)** | — |

---

## 2. Baselines (§22.2) — same declared budget for all
| Config | Description | Interval recall | Temporal F1 | Empty-set F1 | Macro-F1 |
|---|---|---|---|---|---|
| B1 | whole-query SigLIP2 frame retrieval | — | n/a | — | n/a |
| B2 | atom-union, no per-constraint verification | — | n/a | — | n/a |
| B3 | B2 + one whole-query VLM yes/no | — | — | — | — |
| FULL | atom union + clip + assembly + constraint verifier | — | — | — | — |
| FULL+GRAPH | FULL + tracklet/graph + bounded logic | — | — | — | — |
| FULL+STR | FULL + trained temporal reranker (only if trained) | — | — | — | — |

---

## 3. Ablations (§22.3), priority order
| # | Ablation | Arm A | Arm B | Metric focus | A | B | Decision |
|---|---|---|---|---|---|---|---|
| 1 | Window aggregation | max/mean/top-20%-mean/persistence | — | candidate recall | — | — | — |
| 2 | Retrieval breadth | whole-query | atom-union | candidate recall | — | — | — |
| 3 | Sampling | base | motion densification | action/order recall | — | — | — |
| 4 | Clip lane | frame | frame+clip | action/order recall | — | — | — |
| 5 | Assembly | none | temporal assembly | temporal F1 | — | — | — |
| 6 | Verifier | whole-query | structured constraint | macro-F1 | — | — | — |
| 7 | Recovery | single call | focused per-atom | macro-F1 | — | — | — |
| 8 | Track/graph union | semantic-only | +track/graph | candidate recall | — | — | — |
| 9 | Grounding source | candidate-only | archive tracklet memory | graph recall | — | — | — |
| 10 | Bounded logic | disabled | enabled | bounded-logic correctness | — | — | — |
| 11 | Ordering | RRF | trained reranker | VLM calls/query | — | — | — |
| 12 | Reranker heads | relevance-only | +per-atom | macro-F1 | — | — | — |
| 13 | Encoder | base SigLIP2 | larger SigLIP2 | candidate recall | — | — | — |

---

## 4. Confidence intervals
Report bootstrap CIs (§25.1 week 3) for the headline metrics once measured. `not_yet_measured`.

---

## 5. Notes / anomalies
_Record cache-hit assertions (must be zero misses bypassed), retries, OOMs, and any lane
disabled by its gate. A budget-truncated run is never reported as a clean no-match._
