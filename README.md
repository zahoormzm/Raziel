# RAZIEL

**Temporal Evidence Intelligence**

> **New to the project, or helping with footage and annotation?** Read
> [`START_HERE.md`](START_HERE.md) instead of this file. It assumes no prior
> knowledge and explains what we need from you and why.

RAZIEL is a local-first system for searching a declared scope of recorded
surveillance footage for zero, one, or multiple supported occurrences of a
natural-language description. **Eyes of God** names only the recall-first
retrieval subsystem. The implementation follows
[`RAZIEL_Master_Execution_Plan_v1.3.md`](RAZIEL_Master_Execution_Plan_v1.3.md);
the plan remains authoritative when this overview is incomplete.

The core pipeline is:

> Parse → retrieve broadly → assemble temporal evidence → rerank → verify each
> constraint → refine boundaries → show evidence → export a traceable clip.

## Truthfulness contract

- Results are conditional on the declared camera/time scope, sampling policy,
  candidate generation, assembly, and verification budget.
- Every required constraint resolves independently to `supported`,
  `contradicted`, `unobservable`, or `undetermined`.
- A budget-truncated search is never rendered as a clean no-match.
- Anonymous tracklets are scoped to one camera/session and never represent
  biometric or real-world identity.
- Visible absence and bounded counts run only in an assessable, continuous
  observation interval with safe coverage.
- Export manifests are traceable extraction records, not legal-admissibility or
  tamper-proofness claims.

## Current implementation surface

The repository contains the five composable lanes described in §26:

1. `ingest/`, `index/`, and `evidence/` — PTS-safe video memory, exact indexes,
   anonymous tracklets, and the typed SQLite evidence graph.
2. `query/` — bounded parsing, fixed graph patterns, recall-first fusion,
   clustering, and temporal assembly.
3. `ml/temporal_reranker/`, `query/verify.py`, and `grounding/` — gated learning,
   four-state verification, boundary refinement, and candidate-only spatial
   evidence.
4. `data/` and `eval/` — ledger-first annotation and held-out science.
5. `packages/contracts/`, `api/`, `ui/web/`, and `output/` — shared contracts,
   orchestration, the product surface, and traceable release artifacts.

Optional model lanes are disabled until their exact master-plan gates pass.
Configuration uses `not_yet_measured` rather than placeholder scores.

## Development environment

The intended core runtime is Python 3.11. Model stacks use separate locked
environments:

- `requirements/core.lock`
- `requirements/cuda130.lock`
- `requirements/verifier.lock`
- `requirements/optional_video_embedding.lock`

Create a local test environment and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

Record the machine surface without converting inventory into a gate result:

```powershell
.\.venv\Scripts\python.exe scripts\verify_environment.py
```

Install `requirements/cuda130.lock` before either CUDA model environment. The
RTX 5070 profile is in `config/hardware/rtx5070.yaml`. Only one large CUDA
model is assumed resident at a time; the exclusive lease modes are `index`,
`train`, and `serve`.

## Local API and interface

After installing the core API dependencies:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

The single-screen interface is served at `http://127.0.0.1:8000/`. Public brand
strings are loaded from `config/brand.yaml`; they are not duplicated in the UI.

After running the local B1 benchmark and staging its exact SigLIP2 revision, the
measured retrieval-only tier can be served with an explicit development
threshold (the repository intentionally provides no hidden threshold default):

```powershell
.\.venv\Scripts\python.exe scripts\serve_local_retrieval.py --threshold 0.10
```

This synthetic smoke operating point is for functional/latency validation only.
It is not a held-out semantic result. Without `--verifier-url`, retrieval-only
service results cannot become verified matches. Connecting the staged worker
enables functional verification, but does not convert synthetic smoke evidence
into a semantic measurement or close Gate G6.

To run the pinned verifier worker after B2 passes, use two terminals and bind
both services to loopback:

```powershell
$opHash = (Get-FileHash .\config\operating_point.yaml -Algorithm SHA256).Hash.ToLower()
.\.venv\Scripts\python.exe -m api.verifier_worker `
  --model .\models\qwen3-vl-4b-instruct `
  --revision ebb281ec70b05090aa6165b016eac8ec08e71b17 `
  --operating-point-hash $opHash
```

```powershell
$opHash = (Get-FileHash .\config\operating_point.yaml -Algorithm SHA256).Hash.ToLower()
.\.venv\Scripts\python.exe scripts\serve_local_retrieval.py `
  --threshold 0.10 `
  --verifier-url http://127.0.0.1:8010 `
  --verifier-revision ebb281ec70b05090aa6165b016eac8ec08e71b17 `
  --operating-point-hash $opHash
```

The orchestrator validates worker model/config identity before reporting it
healthy. Candidate bundles contain 8–24 source-PTS-labeled frames, and invalid
structured output follows the single schema-retry path.

Primary endpoints:

- `POST /ingest`, `GET /ingest/{job_id}`
- `POST /query`, `GET /query/{search_id}`
- `POST /export`
- `GET /coverage`
- `GET /benchmark/current`
- `GET /health`

## Release discipline

The mandatory core stays demoable while gated lanes run in shadow mode. Before a
release candidate:

1. run the contract and unit suite;
2. run the golden two-minute fixture and ten-query suite cache-bypassed;
3. verify source, embedding, schema, model, and operating-point hashes;
4. test one accurate evidence export and its canonical manifest hash;
5. disconnect the GPU worker and confirm the disclosed fallback state;
6. freeze models, prompts, thresholds, graph schema, datasets, and the demo
   archive at the plan’s decision point.

See `scripts/demo_runbook.md` for the audience-facing preflight and failure
ladder.
