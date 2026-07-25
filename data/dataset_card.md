# RAZIEL evaluation dataset card

**Project:** RAZIEL — Temporal Evidence Intelligence. Retrieval subsystem: *Eyes of God*.
**Owner:** Member 4 — Data and Science.
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §21.7, §21.5, §21.6, §26.1.
**Status of this revision:** **SYNTHETIC SEED.** Every artifact here is a deterministic
synthetic fixture (`synthetic: true`) produced by `data/tools/build_seed_dataset.py`. It
is the schema-valid scaffold that human annotators replace, family by family, once
authorized footage exists. **No real footage, no real held-out results, no invented
numbers.** Numeric agreement and evaluation metrics remain **`not_yet_measured`** until
Gate G9 (§21.8) is met with real footage.

Regenerate/verify: `python data/tools/build_seed_dataset.py` (writes) or `--check` (validate only).

---

## 1. Pool and session counts

| Metric | Value |
|---|---|
| Query families | **42** (minimum 40; target 60–80; stretch 100+) |
| Scenarios (split units) | 19 |
| Sessions | 19 (+1 golden-suite session) |
| Footage/session manifests | 20 (immutable, content-hashed) |
| Paraphrases per family | 2 (independently authored) |
| Annotation records | 30 |
| All synthetic | **yes** |

Pool split: **staged 38, organizer 4.** Organizer families are placeholders with
**authorization pending** (§21.2); they are kept in a separate pool and never mixed with
staged scenarios.

---

## 2. Split policy (§21.5)

- **Split by scenario/session**, never by adjacent windows or file names. Each `scenario_id`
  maps to exactly one split; each `session_id` appears under exactly one split.
- Staged and organizer pools are kept strictly separate.
- Test ground truth is **human-derived** (`ground_truth_source = human_ledger`); retriever
  proposals never define test truth. Auto-proposals may accelerate development annotation only.
- Empty-set (`cardinality=zero`) test families require **full human review** of the declared
  scope (`empty_set_review.review_complete = true`).

Current split distribution (families): **train 20, dev 7, test 15.** Enforced by
`eval.schema.check_split_discipline` (see the test suite).

Cardinality distribution: **one 28, many 1, zero 13** (zero includes true-no-event,
visible-absence, unobservable, ambiguous, and long-gap-identity-rejection families).

---

## 3. Query and capability distribution

Capabilities exercised (family counts; families may carry several tags):

| Capability | Families |
|---|---|
| object | 15 |
| attribute | 14 |
| action | 13 |
| location | 13 |
| temporal_order | 7 |
| binding | 6 |
| empty_set | 6 |
| unobservable | 4 |
| bounded_count | 3 |
| bounded_or | 3 |
| visible_absence | 3 |
| ambiguous | 2 |
| long_gap_identity_rejection | 2 |
| multi_occurrence | 1 |

The **test** split independently covers object, attribute, binding, temporal_order,
empty_set, unobservable, visible_absence, and bounded_count (checked in
`eval/tests/test_dataset.py`).

---

## 4. Challenger distribution (§21.4)

Typed challengers attached to families (counts):

| Challenger type | Count |
|---|---|
| wrong_attribute | 5 |
| wrong_order | 4 |
| wrong_binding | 3 |
| partial_event | 2 |
| wrong_object | 1 |
| short_interruption | 1 |
| visually_similar_actor | 1 |
| unobservable | 1 |
| true_no_event | 1 |
| repeated_events | 1 |
| track_fragmentation | 1 |
| bounded_disjunction | 1 |
| visible_absence_assessable | 1 |
| visible_absence_unassessable | 1 |
| bounded_count_correct | 1 |
| bounded_count_incorrect | 1 |

The full §21.4 type vocabulary is enforced by `challenger.schema.json`. The seed density is
deliberately light (near-miss authoring is a human task over real footage); the required
spread of types is present and checked by the test suite.

---

## 5. Annotation protocol (§21.1, §21.6)

- **Ledger-first.** A human watches the footage in full and writes the event ledger
  (`data/ledger.md`, `ledger_entry.schema.json`). Query families are built from the ledger;
  retrieval output is never a ground-truth source.
- **Anonymous actors.** Actors are per-camera/session ids (`P1`, `P2`, …), never identities.
- **Four states.** supported / contradicted / unobservable / undetermined; human labels use
  only the first three (`undetermined` is a system-failure state).
- **Blind double annotation.** ≥20% of families receive two independent, blind passes,
  adjudicated only after both are recorded.

Double-annotation coverage in this seed: **10 / 42 families = 0.238 (≥ 0.20).**

---

## 6. Agreement (§21.6)

Inter-annotator agreement (present/absent, atom-state, relation, temporal-boundary IoU) is
**`not_yet_measured`**. The seed's two independent passes are identical by construction (they
are generated, not human), so no real disagreement statistic can be reported yet. Real
agreement numbers require human second-annotators over authorized footage.

---

## 7. Staged vs organizer provenance and authorization

| Pool | Families | Authorization | Notes |
|---|---|---|---|
| staged | 38 | authorized (synthetic placeholder) | §21.2 staged scenarios: repeated event, black/blue-bag decoy, wrong-binding/order/partial decoys, dark/occluded unobservable, true empty-set, cross-window event, bounded-count with occlusion, bounded-OR, visible-absence assessable + occluded |
| organizer | 4 | **pending** | placeholder until real organizer footage is delivered and a real manifest is sealed (§21.2, §32) |

Every manifest records `authorization.status`, `consent_recorded`, and retention policy.
Manifests are content-hashed and immutable.

---

## 8. Known biases and limitations

- **Synthetic seed.** Distributions, boundaries, and challenger density are illustrative, not
  representative of real surveillance footage. Do not read model capability into any number here.
- **No real agreement/eval numbers.** All held-out metrics are `not_yet_measured`.
- **Light challenger density.** One or two challengers per family; real annotation will expand these.
- **Organizer pool is a placeholder.** Real organizer footage, codecs, and hours are unknown
  until delivery (§32 organizer confirmations).
- **Scope-conditional claims only.** Nothing here supports claims of watching every frame,
  proven absence, cross-camera/long-gap identity, tamper-proofness, or legal admissibility.

---

## 9. Path to a real dataset (Gate G9, §21.8)

Outstanding, and **not fabricable**:

1. Authorized footage (organizer and/or staged) with sealed **real** manifests.
2. A complete **human ledger** over that footage.
3. ≥40 human-authored families (target 60–80) built from the ledger, replacing this seed.
4. Two independently written paraphrases per family by real second authors.
5. Blind double annotation of ≥20% with recorded agreement and adjudication.
6. Real held-out system predictions (Members 1–3) to move any metric off `not_yet_measured`.

G9 acceptance (structural, met by this seed): ledger schema+protocol complete; ≥40 families;
challenger coverage present; independent-agreement subset structure present; the evaluation
script runs end to end. The **numeric** G9 bar is met only with items 1–6 above.
