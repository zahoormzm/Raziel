# RAZIEL operating-point recommendation — TEMPLATE

**Project:** RAZIEL — Temporal Evidence Intelligence. Retrieval subsystem: *Eyes of God*.
**Owner:** Member 4 — Data and Science.
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §25.1 (week-3 decision), §22, §9.4/§9.5,
§16.6, §5.1 gates, §25.4 release fence.

> **No-invented-numbers rule.** This document selects the **primary event configuration**
> from *measured* held-out results and gate outcomes. Gate **thresholds** below are quoted
> from the frozen plan (they are fixed acceptance bars, not results). Every **measured**
> cell is `not_yet_measured` until a real held-out evaluation and a real hardware benchmark
> fill it. Fill a cell only from `eval/reports/benchmark_panel.json` (metrics) or the
> `scripts/day1_bench.py` outputs (hardware). Copy this file to
> `data/operating_point_recommendation_<date>.md` before filling it.

---

## 0. Decision inputs

| Input | Source | Value |
|---|---|---|
| Held-out benchmark panel | `eval/reports/benchmark_panel.json` | config `not_yet_measured` |
| Hardware benchmarks B1–B7 | `scripts/day1_bench.py` (Member 1/3) | `not_yet_measured` |
| Verifier gate G6 | labeled verifier set (§17.6) | `not_yet_measured` |
| Reranker ship gate | shadow-mode held-out (§16.6) | `not_yet_measured` |
| Dataset revision | manifest hashes | `not_yet_measured` |

The **test** labels used here are controlled solely by the data lane and are **never** used to
tune thresholds (§26.2.8). Threshold tuning uses development data only.

---

## 1. Mandatory-core gate checklist (must all be green — §25.4 release fence)

| Gate | What it certifies | Threshold (plan) | Status |
|---|---|---|---|
| G1 | PTS ingest, coverage denominator, restart idempotence | §11.4 | `not_yet_measured` |
| G2 | Frame embeddings, exact score vector, smoke retrieval | §12.5 | `not_yet_measured` |
| G3 | Parser on ≥25 scripted queries | ≥80% fully correct (§13.4) | `not_yet_measured` |
| G4 | Candidate recall on dev | ≥95% where feasible (§14.4) | `not_yet_measured` |
| G5 | Cross-window assembly; wrong-order contradicted | §15.3 | `not_yet_measured` |
| G6 | Required-condition semantic macro-F1 | ≥0.70 (§17.6) | `not_yet_measured` |
| G8 | Preview/evidence export; manifest canonical hash | §20.4 | `not_yet_measured` |
| G9 | Dataset: ≥40 families, challengers, agreement subset, eval runs | §21.8 | structural: **met (synthetic seed)**; numeric: `not_yet_measured` |

No gated enhancement joins the primary demo while any mandatory core gate is red (§25.4).

---

## 2. Hardware gates (§9.4) — condition each lane

| Gate | Measures | Keep condition (plan) | Status |
|---|---|---|---|
| B1 | sample + SigLIP2 embed 10 min | ≥5× real-time preferred | `not_yet_measured` |
| B2 | Qwen3-VL 4B verify 12 s cand | warm median ≤20 s, ≥1.5 GB headroom | `not_yet_measured` |
| B3 | Qwen3-VL 8B verify | same gate + held-out semantic gain | `not_yet_measured` |
| B4 | native clip embeddings | fits + practical pre-event indexing rate | `not_yet_measured` |
| B5 | parser | warm median ≤5 s else deterministic fallback | `not_yet_measured` |
| B6 | ten-query end-to-end | retrieval ≤3 s; verified ≤30 s (ceiling 60 s) | `not_yet_measured` |
| B7 | detection + tracklets 10 min | practical overnight rate; resume reproduces hashes | `not_yet_measured` |

---

## 3. Per-lane recommendation (fill from measurements)

Each optional lane ships in the primary demo **only** if its gate passes; otherwise it stays a
reproducible ablation and the lane remains `not_yet_measured` here.

| Lane | Feature flag | Ship criterion (plan) | Measured delta | Recommendation |
|---|---|---|---|---|
| Motion densification | — | improves action/order candidate recall (§5.1) | `not_yet_measured` | `not_yet_measured` |
| Native clip lane | `clip_lane` | B4 passes + material action/order recall gain (§12.2) | `not_yet_measured` | `not_yet_measured` |
| Tracklet + evidence graph | `tracklet_lane`, `evidence_graph` | G2 track precision/fragmentation OK; edges inspectable (§12.5) | `not_yet_measured` | `not_yet_measured` |
| Bounded logic | `bounded_logic` | correct outcomes; zero false clean-negatives (§22.1(10)) | `not_yet_measured` | `not_yet_measured` |
| Temporal reranker | `temporal_reranker` | ≥3-pt temporal F1 **or** ≥25% fewer VLM calls at equal recall; rejection F1 not materially down; macro-F1 not down; complete-set recall not down; <1 s/query; RRF fallback (§16.6) | `not_yet_measured` | `not_yet_measured` |
| Spatial grounding | `grounding` | G7: valid boxes, no gross errors, fits latency, off≠verdict (§18.3) | `not_yet_measured` | `not_yet_measured` |
| Mac MLX verifier fallback | `mac_verifier` | meets min semantic + latency gate (§9.6) | `not_yet_measured` | `not_yet_measured` |

---

## 4. Recommended operating point

| Field | Value |
|---|---|
| Operating tier (§9.5) | `not_yet_measured` (A dual-lane / B reliable / C degraded) |
| Primary configuration | `not_yet_measured` (FULL / FULL+GRAPH / FULL+STR) |
| Enabled feature flags | `not_yet_measured` |
| Operating-point config hash | `not_yet_measured` |
| t-IoU threshold | 0.5 (declared) |
| Declared verification budget | candidate ≤32 / verification ≤24 clusters / ≤60 s |
| **Rollback configuration** | **FULL (reliable P0, day-7 tag)** — the permanent known-good fallback (§25.1) |

**Selection rationale:** _one paragraph, citing the measured panel and gate outcomes above.
State each rejected lane and the measured reason it was rejected (a rejected lane remains a
reproducible ablation, §25.1). Do not invent numbers._

---

## 5. Wording discipline for the chosen point (§2)

Any public statement of this operating point must remain conditional: "At the current
operating point," "All successfully embedded sampling ticks in the declared scope were
scored," "Measured on our held-out set." It must never claim every source frame was watched,
proven absence, cross-camera/long-gap identity, tamper-proofness, legal admissibility, or
uncalibrated confidence.
