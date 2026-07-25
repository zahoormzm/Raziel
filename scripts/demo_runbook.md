# RAZIEL demo runbook

This operational checklist implements §27 of the master plan. It does not
replace the release gates or authorize unmeasured optional lanes.

## Before the audience enters

1. Connect the M4 Max product node and RTX 5070 worker over the dedicated wired LAN.
2. Disable sleep, updates, and background synchronization on both nodes.
3. Compare source, embedding, schema, model, and operating-point hashes.
4. Start the Mac orchestrator and the versioned 5070 GPU worker.
5. Run health checks, one uncached warm query, exact seek, and one evidence export.
6. Disconnect the worker and verify the tested fallback state and `undetermined` behavior.
7. Reconnect, clear the demo ledger, and connect the projector to the Mac only.

## Six-minute story

Use the six beats and exact wording from §27.2. Do not improvise identity,
absence, completeness, calibration, or legal claims. At least one query must
bypass the result cache. Every cached response must show the `cached` badge.

## Failure ladder

- Healthy worker: full local retrieval plus 5070 verification/grounding.
- Slow worker: preserve retrieval feedback and disclose/reduce the verification budget.
- Unavailable worker: use the separately gated MLX verifier or labeled retrieval-only mode.
- App failure: show the backup recording and label it as a recording.

No speculative change is permitted after the event freeze.
