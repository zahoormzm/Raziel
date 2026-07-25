# CR-001 — standardize on `continuous_camera_interval`

| Field | Value |
|---|---|
| **Raised by** | Member 4 — Data and Science |
| **Owner of change** | Member 2 — Query intelligence (`query/parser.py`, `query/schema.py`) |
| **Coordinator** | Member 5 — release owner (§26.2.7 schema releases) |
| **Affected consumers** | Member 4 (schemas, golden suite, validators), Member 3 (verification inputs) |
| **Status** | **Open — approved in principle, not yet applied** |
| **Raised** | 2026-07-26 |
| **Blocks** | Freezing schema version 1.0 |

---

## Decision

The canonical token is **`continuous_camera_interval`**. `camera_time_interval` is retired.

Approved by the release owner on 2026-07-26; recorded in
[`RELEASE_STATUS.md`](../../RELEASE_STATUS.md).

## Why

The safety semantics live in the word **continuous**. Both `visible_none` and bounded `count`
are certifiable *only* inside a continuous observation interval with complete expected
observation ticks — that is the precondition enforced by
`eval.schema._track_logic_safety_errors` and asserted in [`../ledger.md`](../ledger.md) §4.

`camera_time_interval` names the interval without naming the property that makes a claim over
it safe. A future reader can satisfy `camera_time_interval` with a discontinuous window and
produce a clean negative that the truthfulness contract forbids. The token should make the
unsafe reading hard to write.

## Current split

| Token | Locations |
|---|---|
| `camera_time_interval` | `query/parser.py`, `query/schema.py` |
| `continuous_camera_interval` | `data/schemas/atom_relation_state.schema.json`, `data/schemas/track_logic_ground_truth.schema.json`, `data/queries/families/fam_0011.json`, `fam_0012.json`, `fam_0013.json`, `data/tools/build_seed_dataset.py`, `eval/tests/test_schema.py`, `tests/golden/suite/golden_10_bounded_count.json` |

Member 4's lane is already on the target token. The change is confined to Member 2's two
files plus any transitively affected fixtures.

## Requested change

1. **Member 2** renames `camera_time_interval` → `continuous_camera_interval` in
   `query/parser.py` and `query/schema.py`, and writes the migration note per §26.2.7
   ("the producing member writes migrations").
2. **Member 2** confirms the compiler genuinely requires continuity where the token is used —
   the rename must not be cosmetic. If any current code path accepts a discontinuous interval
   under this name, that is a separate defect to fix, not to rename around.
3. **Member 3** confirms no verification input depends on the old token.
4. **Member 5** sequences the merge and tags the schema release.
5. **Member 4** re-runs `eval/tests` and the §29 golden suite against the renamed contract.

## Acceptance

- [ ] No occurrence of `camera_time_interval` remains outside this document and the historical
      note in `RELEASE_STATUS.md`.
- [ ] `python -m unittest discover -s eval/tests -t .` green.
- [ ] `python tests/golden/test_golden_structure.py` green.
- [ ] Full suite green (181 tests at time of writing).
- [ ] `data/schemas/VERSIONS.md` records the token change as a versioned schema event.
- [ ] Only then may schema version 1.0 be frozen.

## Explicitly out of scope

Member 4 does not edit `query/**`. This request exists because the boundary in §26.1 is real:
the producing lane owns its contract surface, and Member 4 owning frozen-test labels (§26.2.8)
depends on not reaching across that line.
