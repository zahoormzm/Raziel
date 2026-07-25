# RAZIEL authorized footage — collection protocol

**Project:** RAZIEL — Temporal Evidence Intelligence. Retrieval subsystem: *Eyes of God*.
**Owner:** Member 4 — Data and Science.
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §21.2 (staged footage), §21.5 (split
discipline), §11.4 (Gate G1), §21.7 (dataset card provenance).
**Companion documents:** [`ledger.md`](ledger.md) (annotation protocol),
[`footage_shotlist.md`](footage_shotlist.md) (what to record).

This document covers **collection**: where footage lives, what must be recorded about it,
and who does what. It is the precondition for every gate that is currently blocked.

---

## 0. Why footage is the whole critical path

Gates **G1, G2, G4, G6, G7, G9**, the reranker ship gate, and operating-point selection are
all blocked on one input. Nothing downstream can move until footage exists. Treat the
recording day as the single highest-priority milestone on the project.

---

## 1. Where footage lives (and why it is not in git)

```
artifacts/authorized_footage/
├── staged/
│   ├── session_001/cam_01.mp4
│   ├── session_002/cam_01.mp4
│   └── ...
└── organizer/
    └── session_101/cam_01.mp4
```

`artifacts/` is in `.gitignore`. **This is deliberate and must not be changed.** Recorded
footage of consenting participants does not belong in a git history that cannot be rewritten.

The consequence is a hard rule:

> **Footage is untracked. Provenance is tracked.**
> Every file under `artifacts/authorized_footage/` must have a corresponding sealed manifest
> in `data/manifests/<session_id>.json`, which *is* tracked. The manifest carries the
> `logical_path` and `source_sha256` that bind the tracked record to the untracked bytes.

If a session's footage is lost, the manifest still proves what was collected, under what
authorization, and what its hash was.

---

## 2. What must be recorded for every session

Filled into `data/manifests/<session_id>.json` from
[`templates/footage_session_manifest.template.json`](templates/footage_session_manifest.template.json).
Do **not** hand-write `content_hash` — seal with `eval.schema.seal_manifest(...)`.

| Field | Requirement |
|---|---|
| `session_id` | Unique. Matches the directory name under the pool. |
| `scenario_id` | The **split unit**. One scenario → exactly one split. Never straddles. |
| `pool` | `staged` or `organizer`. Never mixed within a scenario. |
| `authorization.status` | `authorized` before a single frame is used for labelling. |
| `authorization.consent_recorded` | `true` only when every visible participant has consented. |
| `authorization.retention_policy` | Explicit, e.g. `delete-after-event`. Not blank. |
| `provenance.collected_by` | Real collector identity (Person 5). |
| `provenance.collection_date` | ISO date of recording. |
| `provenance.staged_scenario_description` | Required for the staged pool. |
| `provenance.organizer_delivery_ref` | Required for the organizer pool. |
| `footage_files[].source_sha256` | **Streamed** SHA-256. Must equal Member 1's ingestion hash. |
| `footage_files[].synthetic` | `false`. Any `true` here means it is not real footage. |
| `synthetic` (top level) | `false`. |

### Consent

Use consenting participants only. Consent is recorded **before** recording, covers the
retention policy, and is per-person. A participant who withdraws consent means that session's
footage is deleted and its manifest marked accordingly — not quietly reused.

Nothing in this dataset asserts biometric identity. Actors are anonymous per-camera ids
(`P1`, `P2`, …) at ledger time regardless of who the participants actually are.

---

## 3. Gate G1 — the uninterrupted 60-minute file

§11.4 requires: one hour ingests without timestamp regression; restart creates no duplicate
ticks; source hash and FFprobe data present; base coverage denominator matches a hand count;
a forced decode failure remains in the denominator.

**The binding constraint is that the hour must be one continuous file with monotonic PTS.**
Most consumer cameras and phones silently split recordings (4 GB FAT32 limit, or a 30-minute
cap). A split file fails G1 outright.

Mitigations, in order of preference:

1. **Record with ffmpeg directly from a fixed USB camera.** This guarantees a single file and
   a clean timebase:

```bash
ffmpeg -f dshow -i video="YOUR_CAMERA_NAME" -t 3600 -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -r 25 -vsync cfr artifacts/authorized_footage/staged/session_001/cam_01.mp4
```

   List camera names first:

```bash
ffmpeg -list_devices true -f dshow -i dummy
```

2. If using a standalone camera: format the card **exFAT** (not FAT32), disable any
   auto-split / loop-record / auto-power-off setting, and confirm on a 10-minute test that a
   single file is produced before committing to the hour.

Additional requirements for the G1 session:

- The camera **does not move at all** for the full hour. Mount it; do not hand-hold.
- Lock exposure and white balance if the camera allows it. Auto-exposure hunting creates
  brightness steps that read as scene changes.
- Constant frame rate (`-vsync cfr` above). Variable frame rate complicates PTS-safe ingestion.
- Verify immediately after recording:

```bash
ffprobe -v error -show_entries format=duration,format_name -show_entries stream=codec_name,r_frame_rate,avg_frame_rate,time_base -of default=noprint_wrappers=1 artifacts/authorized_footage/staged/session_001/cam_01.mp4
```

   `duration` must be ≥ 3600. If it is 1800 or 2700, the camera split the file — re-record.

---

## 3a. The `external` pool (generalization probe)

Schema 1.1.0 adds a third pool, `external`: third-party footage used to test whether the
system works on video we did not stage. Rationale and the full instruction set are in
[`../START_HERE.md`](../START_HERE.md) §4a. The operational rules:

| Rule | Detail |
|---|---|
| **Video only** | The source supplies footage. `ground_truth_source` stays `human_ledger`. Third-party annotations are never imported — if the download shipped label files, do not open them. |
| **Consent provenance first** | Prefer datasets recorded with consenting paid actors. Accept clearly licensed footage with documented provenance. Reject anything scraped or of unclear consent status. |
| **Verify the licence yourself** | Person 5 reads the actual licence and consent documentation before downloading, and records name, URL, licence, consent basis, and check date in the manifest notes. Do not rely on any summary, including one written inside this repository. |
| **Size** | 20–30 minutes total. This is a probe, not a second dataset; every extra minute is Person 1's ledger time. |
| **Authorization field** | `authorization.status` reflects the **licence**, and `consent_recorded` means *the source documented its consent basis* — not that we obtained consent ourselves. Say which, in `authorization.notes`. |
| **Reporting** | External results are reported **separately** from staged. Never averaged in — a combined number hides the exact difference the pool exists to measure. |

Enforced by `eval/tests/test_dataset.py::test_external_pool_never_carries_third_party_truth`.

If consent provenance cannot be established in ~30 minutes of looking, pick a different
source. Ambiguity here is a reason to stop, not to proceed carefully.

---

## 4. Pool discipline

- **Staged, organizer, and external pools never mix within a scenario** (§21.5). Enforced by
  `eval.schema.check_split_discipline`.
- Splits are assigned by scenario **before** anyone sees system results, never after.
- Test ground truth never comes from retriever proposals. Auto-proposals may accelerate
  *development* annotation only.
- Empty-set (`cardinality=zero`) **test** families require full human review of the entire
  declared scope before the family is usable as test truth.

---

## 5. Role assignment (five people)

The separation below is what makes the agreement statistics meaningful. It is not
bureaucracy — collapsing two of these roles into one person invalidates the measurement.

| Person | Role | Does | Must NOT |
|---|---|---|---|
| **1** | Ledger author | Watches every file **completely**; writes one ledger entry per notable event; writes each family's canonical query | Annotate the agreement subset (they know all ground truth) |
| **2** | Paraphrase A + annotator A1 | Writes paraphrase A for every family; blind-annotates the agreement subset | See Person 3's wording or labels |
| **3** | Paraphrase B + annotator A2 | Writes paraphrase B for every family; blind-annotates the agreement subset | See Person 2's wording or labels |
| **4** | Adjudicator | Resolves disagreements **after** both blind passes are recorded | Look at any independent pass before both are written to disk |
| **5** | Data steward | Footage, hashes, manifests, sealing, system runs, evaluation | Author ground truth |

### Ordering constraints (enforced in code, not just here)

`eval.schema.check_annotation_ordering` will fail the build if these are violated:

1. Both independent passes carry `pass_type=independent`, `blind=true`, distinct
   `annotator_id`.
2. Both are written to disk **before** the adjudication record exists.
3. The adjudication record references **exactly two** independent passes and its `recorded_at`
   is **strictly after** both.

Adjudication resolves disagreement into a final label. It **never** retro-edits the
independent passes — their disagreement is data we report in the dataset card.

### Paraphrase independence

Persons 2 and 3 write from the **ledger entry and the canonical query**, in separate
documents, without seeing each other's text. Both set `written_independently=true` with
distinct `author_id`. If one person writes both paraphrases, the family is not usable —
§21.3 requires two independent authors.

### Everyone annotates

§25.2: every member contributes 30–45 minutes to annotation until the minimum dataset and
blind-review subset are complete. The role table above governs *who owns what*, not who is
allowed to help.

---

## 6. Order of operations on collection day

1. **Person 5** confirms authorization and consent for every participant, in writing, before
   recording.
2. **Person 5** records the G1 hour first, while everyone is fresh. Verify with `ffprobe`
   before striking the camera.
3. **Person 5** records the remaining scenario sessions per
   [`footage_shotlist.md`](footage_shotlist.md).
4. **Person 5** computes streamed SHA-256 for every file, fills each manifest, and seals it.
5. **Person 1** begins watching. Nothing else starts until the ledger exists — the ledger is
   the ground-truth backbone, and query families cite it rather than introducing new truth.

Do not begin step 5 before step 4. A manifest sealed after annotation has started cannot
prove the footage was unmodified during labelling.

---

## 7. What this protocol does not permit

- Fabricating a session, a hash, a consent record, or a label.
- Deriving any test-split ground truth from retrieval output.
- Presenting staged footage as organizer footage, or vice versa.
- Marking `synthetic: false` on anything not actually recorded from a camera.
- Claiming every source frame was watched, proven absence, tamper-proofness, legal
  admissibility, or uncalibrated confidence.
