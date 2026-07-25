"""RAZIEL evaluation and data-science lane (Member 4).

Public claim discipline is enforced structurally here: the benchmark panel never
emits a fabricated number (see ``eval.panel`` and the ``NOT_YET_MEASURED``
sentinel in ``eval.schema``), ground-truth labels never include the system-only
``undetermined`` state, and optional lanes stay "not yet measured" until real
held-out results exist.

Modules:
    schema     -- dataset schemas, validation, canonical hashing, split discipline
    metrics    -- every §22.1 dashboard metric
    baselines  -- B1/B2/B3/FULL/FULL+GRAPH/FULL+STR configs and §22.3 ablations
    panel      -- machine-readable benchmark panel (config hash + eval date)
    run_eval   -- CLI orchestrator

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §21, §22, §26.1.
"""

__all__ = ["schema", "metrics", "baselines", "panel", "run_eval"]

DATA_LANE_VERSION = "1.0.0"
