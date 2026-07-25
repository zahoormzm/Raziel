# RAZIEL integration status — 2026-07-24

This report consolidates the four agent-owned implementation lanes with the
separately supplied Member 4 data/science lane. The master execution plan
remains authoritative. This repository is an integrated working build, not yet
a release candidate: gates that require authorized footage, independent human
labels, held-out predictions, Mac hardware, or Docker execution remain open.

## Frozen semantics preserved

- RAZIEL is local-first Temporal Evidence Intelligence.
- Eyes of God names recall-first retrieval only.
- Exact scoring covers every successfully embedded sampling tick in scope.
- Candidate existence is the union of every above-threshold enabled channel;
  RRF orders candidates but never removes them.
- Required constraints resolve to `supported`, `contradicted`, `unobservable`,
  or `undetermined`.
- Incomplete retrieval, unsafe visible absence/counts, occlusion, fragmentation,
  verifier failure, or budget exhaustion cannot become a clean negative.
- Tracklets never claim biometric identity or cross-camera/long-gap identity.
- Export manifests are traceable extraction records, not legal-admissibility
  claims.

## Reproduced checks

| Check | Result |
|---|---|
| Full Python suite | 181 passed |
| Python bytecode compilation | Passed |
| UI JavaScript syntax | Passed with bundled Node.js |
| JSON/YAML parse | 20 JSON and 7 YAML files parsed |
| Python dependency consistency | `pip check` passed |
| Seed dataset validation | 20 manifests, 42 families, 30 annotation records, 23.8% double annotation, 10 golden cases |
| Evaluation runner | Passed; held-out aggregate metrics remain `not_yet_measured` |
| Runtime cleanup | No RAZIEL API or verifier worker left running |

Docker is not installed on this machine. The Dockerfiles and Compose topology
are present and statically reviewed, but the Compose runtime has not been
executed here.

## Measured hardware results

All current measurements use synthetic fixtures for throughput or functional
validation. They are not semantic benchmark results.

| Benchmark | Status | Reproduced result |
|---|---|---|
| B1 SigLIP2 | Passed | 600/600 ticks; 22.8325× real time; 129.8599 embed fps; 1.6632 GiB peak VRAM; 600 exact scores in 0.1497 s |
| B2 Qwen3-VL 4B NF4 | Passed hardware gate | 30 warm calls; 12 s median 15.5680 s; overall p95 24.0117 s; 4.6412 GiB peak; 5.7813 GiB minimum headroom; zero retries/failures/cache hits |
| B3 Qwen3-VL 8B | Measured; failed the memory gate | 30 warm runs; 12 s median 14.5665 s (ceiling 20 s, passes); overall p95 16.9474 s; 7.9182 GiB peak; **1.3227 GiB minimum headroom against a 1.5 GiB floor**; zero retries/failures/cache hits; 28.57 s cold load. Remains disabled. Per §9.2 the 5070 run was scoped as a feasibility test only, and it answered the question. |
| B4 native clip embeddings | Not yet measured | Disabled |
| B5 parser | Passed latency gate | 24 warm varied calls; median 0.000182 s; p95 0.000318 s; zero failures/cache hits. Re-run after the Gate G3 parser fixes; no regression. |
| B6 end to end | Open | Retrieval median 0.0891 s, p95 0.2405 s; one verified vertical slice completed in 20.4086 s; required ten-query verified run not complete |
| B7 detection/tracklets | Not yet measured | Shadow implementation only |

The real code path has completed one synthetic, cache-bypassed vertical slice:
PTS-safe ingestion, exact scoring, recall-first candidate generation, Qwen
verification, evidence export, and canonical manifest hashing. One verified
match was produced in 20.4086 seconds with zero cache hits. The generated
evidence clip SHA-256 is
`642c82481e6004485469662e9bf860e44e675fd331e78a5ff217714535154d38`.

## Gate status

| Gate | Status | Exact remaining proof |
|---|---|---|
| G1 ingestion | Partially proven | The code and restart/timestamp/coverage tests pass; a real authorized one-hour ingest is still required. The hardware run used ten synthetic minutes. |
| G2 indexing/graph | Partially proven | B1 and exact-score coverage pass. Tracklet precision, fragmentation, count error, and inspectable graph quality need a hand-labeled authorized subset. Native clip indexing remains disabled. |
| G3 parsing | **Passed** | 31 scripted queries across all 16 required categories; 30 fully correct (96.8%, threshold 80%); **zero unsafe parses**. Reproduce with `python -m eval.g3_audit`; report at `eval/reports/g3_audit.json`. The audit blocks the gate on any unsafe parse regardless of score, since §13.4 requires failures to be survivable rather than merely infrequent. |
| G4 retrieval | Open | Held-out candidate recall, action/order recall, bounded-logic correctness on real labels, and the primary thresholds are not measured. Thresholds remain `null`; bounded logic remains disabled. |
| G5 assembly | Functional fixture passed | Correct-order, wrong-order, binding, scope, trace, and episode-cap behavior are tested. The plan's staged authorized event remains to be recorded and evaluated. |
| Reranker ship gate | Open | Training/resume machinery exists, but the data gate and held-out gain/call-reduction/latency criteria are not measured. The reranker remains disabled. |
| G6 verification | Open | B2 proves RTX 5070 feasibility only. Required-condition semantic macro-F1, confusion matrix, reason-stratified undetermined rate, and held-out challenger results are not measured. |
| G7 grounding | Open | Candidate-only safety behavior is tested; the required 20 supported real evidence instances and latency/quality measurement are absent. Grounding remains disabled. |
| G8 export | Functionally passed on synthetic source | Preview/evidence export, resolver boundaries, source/output hashes, and canonical manifest validation pass. Repeat on the frozen authorized demo archive before release. |
| G9 dataset | Open | Synthetic seed satisfies structural counts only. Authorized sealed footage, a complete human ledger, ≥40 human-authored families, two real second-author paraphrases per family, real blind agreement/adjudication, and Members 1–3 held-out predictions are required. |

### On the B3 result

The 8B verifier is not a plan change. §9.2 scopes the 5070 run for this model as
a **feasibility test only**, with the full held-out comparison assigned to a
5090/cloud run; the test returned a clean negative and the gate held.

It is not moving to the Mac. §9.6 gives the M4 Max the product node — UI, exact
search, playback, evidence, exports — plus a *separately benchmarked MLX
fallback verifier* with its own semantic and latency gates. That is a different
component from the 8B, and residency on the demo machine competes with the
surface the Mac must serve without the desktop.

The shortfall is small (about 181 MiB) and could be reclaimed by shrinking the
evidence-frame bundle or the token budget. That is deliberately **not** being
done now: both trade verification quality for memory, and the size of that trade
cannot be measured until Gate G9 supplies real footage and labels. §9.4 keeps the
8B only on the same gate **and** a held-out semantic gain, so the second
condition is currently unmeasurable regardless.

The 4B primary path retains 5.7813 GiB of headroom, which is the number that
matters for adding the detector/tracklet lane later.

The selected event operating point is intentionally not frozen:
`config/operating_point.yaml` remains `not_yet_measured` and all primary
thresholds remain `null`.

## Contract decisions — APPROVED 2026-07-26

Both previously open cross-lane ambiguities are now decided by the release owner.

**Decision 1 — preserve both unresolved dimensions. APPROVED.**

When one candidate carries both an `unobservable` and an `undetermined` required
constraint, public/archive aggregation preserves both. No precedence rule is
introduced. Rationale: §4.1 resolves every required constraint independently;
`unobservable` (the footage cannot show it) and `undetermined` (the system failed
to decide) have different causes and different remedies, and collapsing them
would destroy the reason-stratified undetermined rate that Gate G6 requires.
Current behaviour already complies — verification output preserves both and
archive conclusion generation refuses to invent precedence — so this decision
ratifies the implementation rather than changing it.

**Decision 2 — standardize on `continuous_camera_interval`. APPROVED.**

`continuous_camera_interval` is the canonical token for schema version 1.0.
Rationale: the safety semantics live in the word *continuous* — visible absence
and bounded counts are certifiable only inside a continuous observation
interval, and `eval.schema._track_logic_safety_errors` depends on that meaning.
`camera_time_interval` is silent about the property that makes the claim safe.

This decision is **not yet applied in code.** `camera_time_interval` remains in
`query/parser.py` and `query/schema.py`, which are Member 2's lane. Per §26.2.7
the rename proceeds as a contract request coordinated by Member 5:
[`data/contract_requests/CR-001-continuous-camera-interval.md`](data/contract_requests/CR-001-continuous-camera-interval.md).
Schema 1.0 must not be frozen until the rename lands and both lanes are green.

## External work needed for release

1. Supply authorized organizer/staged footage and seal real manifests.
2. Complete the human ledger, second-author paraphrases, blind review, agreement,
   and adjudication.
3. Produce held-out prediction bundles from Members 1–3 and run the full
   baselines/ablations under one declared budget.
4. Select thresholds only from development measurements, then freeze and run
   the ten-query cache-bypassed B6 suite.
5. Execute the Mac replica/failover topology and Docker clean-machine checks.
6. Run the final authorized-archive export, hash verification, and three
   uncached rehearsals.
