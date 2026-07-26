# RAZIEL data-lane schema registry

**Owner:** Member 4 — Data and Science.
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §21, §22, §26.1.

These are the **dataset-lane** schemas. They are distinct from the boundary
**contracts** in `packages/contracts/` (owned by Member 5, produced/consumed by
Members 1–3). Where a dataset artifact must line up with a contract, that
alignment is called out below as a **contract dependency**, not an edit to the
contract.

Schemas are versioned JSON Schema (draft 2020-12, validated by a stdlib subset
validator in `eval/schema.py` — no external `jsonschema`/`PyYAML` dependency, per
the §8.4 offline rule). Every data file carries a `*_schema_version` field pinned
by `const` in its schema.

| Schema file | Version | Artifact | Notes |
|---|---|---|---|
| `footage_session_manifest.schema.json` | 1.1.0 | Immutable footage/session manifest | Content-hashed (`content_hash` = SHA-256 over canonical JSON minus that field). **Contract dependency:** `FootageFile.source_sha256` must equal the streamed SHA-256 that ingestion (`ingest/hash_source.py`, Member 1) computes for the same file. |
| `ledger_entry.schema.json` | 1.0.0 | Event ledger entry | `provenance.source` is fixed to `human_watch`. Actors are anonymous per-camera ids. |
| `query_family.schema.json` | 1.1.0 | Query family | Composes interval / atom-relation / challenger / assessability-boundary / track-logic schemas via `$ref`. Exactly two independent paraphrases. **Contract dependency:** family `atoms`/`relations`/`logic_groups` mirror the parser atom schema in §13.1 so labels align with `QueryPlan` (Member 2). |
| `interval.schema.json` | 1.0.0 | Zero/one/many intervals | Cardinality cross-checked against interval count. |
| `atom_relation_state.schema.json` | 1.0.0 | Atom/relation/logic ground-truth states | Ground-truth labels use `supported/contradicted/unobservable` only; `undetermined` is a system output, never a label. |
| `assessability_boundary.schema.json` | 1.0.0 | Assessability + boundary labels | `$defs.Assessability`, `$defs.BoundaryLabel` reused across schemas. |
| `challenger.schema.json` | 1.0.0 | Typed challenger | Full §21.4 type list including track fragmentation, duplicate track, bounded disjunction, visible absence (assessable/unassessable), bounded counts (correct/incorrect). |
| `track_logic_ground_truth.schema.json` | 1.0.0 | OR / visible_none / bounded-count ground truth | Encodes §14.3 safety: unassessable or incomplete-coverage regions can never become a clean negative. |
| `annotation_record.schema.json` | 1.0.0 | Blind double annotation + adjudication | Adjudication only after both independent labels exist. |

## Cross-field rules NOT expressible in JSON Schema

These are enforced by `eval/schema.py` domain checks and covered by tests:

1. **Manifest immutability** — recomputed `content_hash` must match the stored value.
2. **Split discipline** — one `scenario_id` maps to exactly one split; a `session_id`
   never appears under two splits; staged, organizer, and external pools stay separate.
3. **Interval cardinality** — `zero`→0 intervals, `one`→1, `many`→≥2; every `t1 >= t0`.
4. **Paraphrase independence** — exactly two paraphrases with distinct `author_id`.
5. **Retriever-truth prohibition** — `ground_truth_source` must be `human_ledger`;
   ledger `provenance.source` must be `human_watch`.
6. **Empty-set review** — `cardinality=zero` ⇒ `empty_set_review.required=true` and,
   for the `test` split, `review_complete=true`.
7. **visible_none safety** — `visible_absence_supported` requires `assessable=true`
   AND `observed_ticks_complete=true`; else `unobservable`/`unresolved`.
8. **Count safety** — integer `expected_outcome` is illegal when `fragmentation_level=high`
   or `occlusion_level=heavy` (must be `unresolved`); integer counts must be `<= declared_bound`.
9. **Adjudication ordering** — an `adjudication` record references exactly two `independent`
   records and its `recorded_at` is strictly after both of theirs; double-annotated
   independent passes are `blind=true`.
10. **External-pool truth** — an `external` family's `ground_truth_source` is still
    `human_ledger`. Third-party datasets supply *video only*; their annotations are
    never imported as ground truth.

## Change policy

A schema change is a versioned event: bump the `const` version **and the matching
`$id` suffix**, migrate every fixture, update this table and the affected cross-field
checks, and re-run the owned test suite. No silent edits.

`$id` is called out explicitly because it drifted once: 1.1.0 bumped the `const` on
`query_family` and `footage_session_manifest` while both `$id` values still ended in
`@1.0.0`, so two distinct documents claimed one identifier. `eval/schema.py` resolves
`$ref` by filename so nothing broke, but anything that caches or resolves by `$id`
would have received the wrong contract.

## Change log

### 1.1.0 — 2026-07-26 — `external` provenance pool

`footage_session_manifest.schema.json` and `query_family.schema.json` bumped
1.0.0 → 1.1.0. The `pool` enum gains a third value, `external`.

**What it is.** A generalization probe. Staged footage is recorded by us, which
means we stage the events, write the queries, *and* write the labels — three
degrees of freedom held by one team. The `external` pool answers the obvious
question that follows: does the system work on footage we did not design to be
findable?

**The rule that makes it safe.** External footage supplies **video only**. Labels
always come from our own human ledger under our own protocol
(`ground_truth_source = human_ledger`, unchanged). A third-party dataset's
annotations are never imported as ground truth — their label model is not ours
(no four-state evidence, no assessability, no bounded-count or visible-absence
safety conditions) and their protocol is unknown to us. Enforced by
`eval/tests/test_dataset.py::test_external_pool_never_carries_third_party_truth`.

**Additive and backward compatible.** Every 1.0.0 artifact remains valid; the
version bump follows the change policy above rather than any incompatibility.
The seed dataset was regenerated via `data/tools/build_seed_dataset.py`.

**Migration:** none required for existing data. Pool-separation and split
discipline are string-generic and needed no logic change.
