# RAZIEL data lane

**Project:** RAZIEL — Temporal Evidence Intelligence. Retrieval subsystem: *Eyes of God*.
**Owner:** Member 4 — Data and Science. **Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §21, §22, §26.1.

The dataset and evaluation are first-class deliverables (§33.16). This lane alone controls the
frozen-test labels and prevents tuning on them (§26.2.8).

## Map

```
data/
├── README.md                         this file
├── ledger.md                         ledger-first annotation protocol (§21.1, §21.6)
├── dataset_card.md                   dataset card (§21.7)
├── operating_point_recommendation.md operating-point recommendation TEMPLATE (§25.1)
├── schemas/                          versioned JSON Schemas + VERSIONS.md + cross-field rules
├── templates/                        human authoring skeletons (guidance)
├── manifests/                        immutable, content-hashed footage/session manifests
├── queries/families/                 the query families (>=40; target 60-80)
├── challengers/                      standalone challengers (challengers are also embedded per family)
├── annotations/                      blind double annotation + adjudication records
└── tools/build_seed_dataset.py       deterministic SYNTHETIC seed generator

eval/                                 metrics, baselines, panel, orchestrator, tests, reports
tests/golden/suite/                   the §29 ten-query golden suite (fixtures)
```

## Current status

**Synthetic seed.** All artifacts are deterministic synthetic fixtures (`synthetic: true`).
Every held-out metric is `not_yet_measured`. See `dataset_card.md` for counts and the path to a
real dataset (Gate G9, §21.8).

## Regenerate / validate

```bash
python data/tools/build_seed_dataset.py            # write the seed
python data/tools/build_seed_dataset.py --check    # validate in memory only
python -m eval.run_eval                             # dataset report + not_yet_measured panel
```

## Run the owned tests

```bash
python -m unittest discover -s eval/tests -t . -v   # metrics, schema discipline, baselines, panel, dataset
python tests/golden/test_golden_structure.py -v     # §29 golden suite structure
```

## Guarantees enforced in code (with tests)

- **Immutability** — manifests are content-hashed; tampering is detected.
- **Split discipline** — scenario→one split; no session leakage; staged/organizer pools separate.
- **No retriever truth** — ledger source is `human_watch`; family truth source is `human_ledger`.
- **Malformed/unsupported labels rejected** — states limited to supported/contradicted/unobservable
  (labels) and supported/contradicted/unobservable/undetermined (system); logic limited to
  all / bounded any / visible_none / bounded count.
- **visible_none / count safety** — missing coverage or unsafe fragmentation/occlusion can never
  become a clean negative or a hard count.
- **Blind double annotation ≥20%** — adjudication only after two independent blind passes.
- **not_yet_measured ≠ number** — the panel never emits a fabricated value.

## Contract dependencies (coordinate before relying on these)

- Footage manifest `source_sha256` must equal ingestion's streamed SHA-256 (Member 1,
  `ingest/hash_source.py`).
- Family `atoms/relations/logic_groups` mirror the parser atom schema (§13.1) so labels align
  with `QueryPlan` (Member 2, `packages/contracts/query_plan.py`).
- Predictions bundles consumed by `eval/run_eval.py` are produced by the system under test
  (Members 1–3); the shape is documented in `eval/baselines.py`.
- The sibling integration video fixture `tests/golden/golden_synthetic.mp4` (another member) is
  an engineering fixture, **not** a semantic benchmark; semantic/verifier truth comes from this
  lane only.
