# RAZIEL event ledger — annotation protocol

**Project:** RAZIEL — Temporal Evidence Intelligence. Retrieval subsystem: *Eyes of God*.
**Owner:** Member 4 — Data and Science.
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §21 (dataset & annotation), §21.6
(agreement), §14.3 (bounded-logic safety), §26.2.8 (protocol integrity).

> **Ledger-first, not retriever-first.** The ledger — written by a human watching the
> footage in full — is the ground-truth backbone. Retrieval output is **never** a source
> of ground truth. Auto-proposals may accelerate *development* annotation but never define
> **test** truth (§21.5). This lane alone controls the frozen-test labels and prevents
> tuning on them (§26.2.8).

This document is the procedure. The machine-readable shape is
`data/schemas/ledger_entry.schema.json` (validated by `eval/schema.py`). Every rule below
maps to a field in that schema or a cross-field check in `eval.schema`.

---

## 0. Non-negotiable semantics (carry these into every entry)

- Actors are **anonymous** per-camera/session ids (`P1`, `P2`, …). Never a name, a face,
  a biometric descriptor, or a cross-camera/cross-gap identity claim.
- We never record "the same person" across a discontinuous gap or across cameras. Short,
  visually-continuous same-actor binding **within one episode** is the only identity we assert.
- The four evidence states are **supported, contradicted, unobservable, undetermined**.
  `undetermined` is a *system* failure state; a human ledger/label uses only
  **supported / contradicted / unobservable**.
- `visible_none` (visible absence) is only certifiable inside an **assessable** observation
  interval with **complete** expected observation ticks. Missing coverage → `unobservable`
  or `unresolved`, **never** a clean negative.
- Counts are over **qualifying anonymous tracklets in one continuous camera interval** and
  must become **unresolved** under unsafe fragmentation or occlusion.
- We never claim every source frame was watched, proven absence, tamper-proofness, legal
  admissibility, or uncalibrated confidence.

---

## 1. Procedure (order matters)

1. **Seal the footage manifest first.** Before any annotation, record the session in
   `data/manifests/<session>.json` (`footage_session_manifest.schema.json`) and seal it:
   `content_hash` = SHA-256 over canonical JSON with `content_hash` omitted. The manifest is
   immutable; a later edit changes the hash and is rejected by validation. Record
   authorization status and staged-vs-organizer pool here.
2. **Watch the footage in full**, once per camera, before writing any query family. Do not
   skim to a retrieval hit.
3. **Write one ledger entry per notable event** (`ledger_entry.schema.json`). "Notable" =
   any event a plausible natural-language query could target, plus deliberate **confusables**
   (near-misses) that a good system must reject.
4. **Record repeated occurrences** with a shared `repeated_event_group` id (≥3 occurrences
   for the staged repeated event, §21.2).
5. **Record confusables** with `confusable_of` pointing at the event they are confusable with
   (wrong order, partial event, similar actor, blue-vs-black, etc.).
6. **Only after the ledger is complete** do you build query families from it. Query families
   cite ledger entries; they never introduce ground truth that is not in the ledger.
7. **Split by scenario/session** (see §5). Assign the split before, not after, seeing results.
8. **Double-annotate ≥20%** blind, then adjudicate (see §6).

---

## 2. Ledger entry fields

| Field | Meaning | Rule |
|---|---|---|
| `entry_id` | Stable id | Unique within the dataset |
| `session_id`, `camera_id`, `video_id` | Where the event is | Must match a sealed manifest |
| `start_pts`, `end_pts` | Event onset/offset in **source PTS seconds** | PTS-based, never `frame_index/declared_fps`; `end_pts ≥ start_pts` |
| `actors[]` | Anonymous actors | `anon_id` matches `^P[0-9]+$`; appearance cues are broad and non-biometric |
| `objects[]` | Visible objects | `label` + broad `attributes` (colour, type) |
| `actions[]` | Common actions | Optionally reference an actor/object |
| `relations[]` | Actor–object relations | predicate ∈ {carries, near, wears, places, picks_up, follows} |
| `lighting` | good / low_light / dark / backlit / mixed | Feeds assessability |
| `occlusion` | none / partial / heavy | Feeds assessability |
| `repeated_event_group` | Recurrence id or null | Same id across occurrences |
| `confusable_of` | entry_id / description or null | Names the near-miss relationship |
| `assessability` | Can a human judge it? | `overall` ∈ {assessable, partially_assessable, unassessable} + `reasons[]` |
| `provenance` | Who/when/how | `source` is fixed to `human_watch` |

**Assessability is not a verdict.** "The supplied footage did not make this attribute
assessable" is different from "the attribute is false". Record occlusion/darkness/out-of-frame
as `unassessable` reasons so the downstream label can be `unobservable`, not `contradicted`.

---

## 3. From ledger to query family

A query family (`query_family.schema.json`) is the evaluation unit. Each family:

- has a **canonical query** and **exactly two independently written paraphrases** — two
  different authors, `written_independently=true`, distinct `author_id`. Paraphrases are
  *variants*, not independent families (§21.3).
- declares its **scope** (video/camera/time + sampling-policy version); all claims are
  conditional on this scope.
- has **zero / one / many** ground-truth intervals (`interval.schema.json`). Cardinality is
  cross-checked against the interval count.
- carries **atom / relation / logic-group** labels (`atom_relation_state.schema.json`) with
  ground-truth states drawn only from supported/contradicted/unobservable.
- carries **assessability** and **boundary** labels (`assessability_boundary.schema.json`).
- carries **track/logic ground truth** (`track_logic_ground_truth.schema.json`) for OR,
  visible_none, and bounded counts when relevant.
- attaches **typed challengers** (`challenger.schema.json`).
- sets `ground_truth_source = human_ledger` (retriever truth prohibited) and, for empty-set
  families, `empty_set_review` (see §4).

### Atom labelling rules (mirror §13.2 so labels align with the parser's `QueryPlan`)

- Every atom quotes or maps directly to a span of the request. Do not invent attributes.
- Explicit constraints stay `required=true`. "Maybe X" is an **ambiguity**, not optionality.
- Relations do not become independent object claims.
- The only supported logic is `all`, bounded `any`, `visible_none`, bounded `count`.
- Long-gap `same_actor_required=true` is rejected before retrieval — label the family with the
  `long_gap_identity_rejection` capability and the correct behaviour is one clarification.

---

## 4. Empty-set (visible-absence and true-no-event) discipline

- A `cardinality=zero` family asserts **no verified occurrence in the declared scope at the
  operating point** — it does **not** assert the event can never occur.
- `empty_set_review.required=true` always; for the **test** split
  `empty_set_review.review_complete=true` is required before the family is usable as test truth
  (full human review of the declared scope, §21.5).
- **Visible absence** (`visible_none`) is a stronger, scoped claim. In
  `track_logic_ground_truth.json`, `visible_absence_supported` is legal **only** when
  `assessable=true` AND `observed_ticks_complete=true`. If either fails, the outcome is
  `unobservable` (region unjudgeable) or `unresolved` (missing coverage). This is enforced by
  `eval.schema._track_logic_safety_errors`.
- Always author the **assessable** and the **occluded/unassessable** counterpart together so
  the system is tested on both the clean negative and the honest abstention (§21.2, §29 case 9).

---

## 5. Split discipline (§21.5)

- **Split by scenario/session**, never by adjacent windows or file names. A `scenario_id` maps
  to exactly one split; a `session_id` never appears under two splits.
- **Keep staged and organizer pools separate.** A scenario never mixes pools.
- **Test ground truth never comes from retriever proposals.**
- `eval.schema.check_split_discipline` fails the build on any scenario/session that straddles
  splits or any pool bleed.

---

## 6. Blind double annotation and adjudication (§21.6)

1. Select **≥20%** of families for double annotation (`eval.schema.double_annotation_fraction`
   reports the achieved fraction; target ≥0.20).
2. Two annotators label each selected target **independently and blind**
   (`annotation_record.schema.json`, `pass_type=independent`, `blind=true`). Neither can see the
   other's labels.
3. Record both independent passes **before** any reconciliation. Measured agreement:
   present/absent, atom-state, relation, and temporal-boundary IoU.
4. **Adjudicate only after** both independent labels exist. The adjudication record
   (`pass_type=adjudication`) references **exactly two** independent passes and its
   `recorded_at` is strictly after both. `eval.schema.check_annotation_ordering` enforces this
   ordering and the blind requirement.

Adjudication resolves disagreements into the final label; it never retro-edits the independent
passes (their disagreement is data we report).

---

## 7. Provenance and integrity guarantees

- Manifests are content-hashed and immutable (§20.3 canonical-hash pattern).
- Ledger `provenance.source` is fixed to `human_watch`; family `ground_truth_source` is fixed to
  `human_ledger`. There is no path for a retriever proposal to become test truth.
- Synthetic fixtures (used only for code tests) carry `synthetic=true` and are never presented
  as real footage or real results.
- Every rule here is covered by the owned test suite (`tests/golden/`, `eval/tests/`). A change
  to the protocol is a versioned schema event (see `data/schemas/VERSIONS.md`), not a silent edit.

---

## 8. What still requires real footage / human work

The schemas, validators, metrics, and a **synthetic** seed dataset exist now. Before the dataset
is real (Gate **G9**, §21.8), the following are outstanding and cannot be fabricated:

1. Authorized footage (organizer and/or staged, §21.2) with sealed real manifests.
2. A **complete human ledger** over that footage.
3. ≥40 human-authored families (target 60–80) built from the ledger, replacing the synthetic seed.
4. Independently written paraphrases (two per family) by real second authors.
5. Blind double annotation of ≥20% plus recorded agreement and adjudication.
6. Real held-out predictions from the system (Members 1–3) to move any metric off
   `not_yet_measured`.
