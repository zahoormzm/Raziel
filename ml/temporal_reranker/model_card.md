# Temporal Evidence Reranker

Status: **not trained / not measured by this repository state**.

This small model consumes frozen, precomputed temporal evidence features. It
predicts candidate relevance, per-atom support hints, optional relation/logic
support hints, frame relevance, event boundaries, and optional evidence
completeness. Its outputs prioritize structured verification and never replace
the four-state evidence verdict.

Training is blocked until `dataset.py` confirms at least 150 independent labeled
candidate episodes, positive and absent examples, all required challenger
families, grouped scenario/session splits, and a manifest certification that the
challenger counts support a confusion table. Relation/logic heads have an
additional label gate.

The model remains in shadow mode unless held-out metrics pass every condition in
§16.6 of `RAZIEL_Master_Execution_Plan_v1.3.md`. Unmeasured or failed gates keep
baseline RRF ordering active.

No Qwen, SigLIP, Grounding DINO, or SAM weights are bundled or downloaded here.
Model revision hashes must be recorded in the dataset and checkpoint manifests.
