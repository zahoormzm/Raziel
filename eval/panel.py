"""Machine-readable benchmark panel with config hash and evaluation date (§22.4).

The panel links no-match conclusions to *measured aggregate* held-out context.
Until a metric is really measured it renders ``not_yet_measured`` — never a
placeholder number. Optional lanes stay ``not_yet_measured`` until real held-out
results exist. Brand strings are read from the frozen configuration (§31.3), not
duplicated.

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §22.4, §2, §31.
"""

from __future__ import annotations

from typing import Any, Optional

from .schema import NOT_YET_MEASURED, canonical_json, is_measured, sha256_hex

PANEL_SCHEMA_VERSION = "1.0.0"

# Frozen brand configuration (§31.3). Mirrors config/default.yaml brand block; the
# authoritative copy lives in shared config owned by Member 5. Kept here as a
# read-only constant so the panel never duplicates ad-hoc brand strings.
BRAND = {
    "product_name": "RAZIEL",
    "product_subtitle": "Temporal Evidence Intelligence",
    "retrieval_name": "Eyes of God",
}

DISCLAIMER = "These are aggregate held-out measurements, not query-specific probabilities."


def eval_config_hash(config_inputs: dict) -> str:
    """Deterministic hash of the resolved *evaluation* configuration.

    This is the eval operating point (thresholds, budget, dataset manifest hashes,
    schema versions, plan version) — distinct from the *system* operating-point
    hash that each predictions bundle carries.
    """
    return sha256_hex(canonical_json(config_inputs))


def build_panel(dataset_summary: dict, sections: dict[str, dict], *,
                eval_config_inputs: dict, evaluated_date: str,
                iou_threshold: float, primary_config: str = "FULL",
                integrity: Optional[dict] = None) -> dict:
    """Assemble the benchmark panel.

    ``sections`` maps config name -> metrics section (from
    ``baselines.compute_config_metrics``). ``primary_config`` supplies the
    §22.4 headline numbers.
    """
    cfg_hash = eval_config_hash(eval_config_inputs)
    headline = _headline(sections.get(primary_config, {}))
    return {
        "panel_schema_version": PANEL_SCHEMA_VERSION,
        "product": dict(BRAND),
        "config_hash": cfg_hash,
        "evaluated_date": evaluated_date,
        "t_iou_threshold": iou_threshold,
        "primary_config": primary_config,
        "dataset": dataset_summary,
        "headline": headline,
        "configurations": sections,
        "data_integrity": integrity or {},
        "disclaimer": DISCLAIMER,
        "wording_discipline": {
            "note": (
                "No-match wording is conditional on the operating point and completeness. "
                "The system never claims every source frame was watched, proven absence, "
                "identity across discontinuous gaps/cameras, tamper-proofness, legal "
                "admissibility, or uncalibrated confidence (§2)."
            ),
        },
    }


def _headline(section: dict) -> dict:
    """Extract the §22.4 headline metrics from a config section."""
    cand = section.get("candidate_recall")
    rc = section.get("required_condition")
    empty = section.get("empty_set_rejection_f1", NOT_YET_MEASURED)

    def frac(num_key: str, den_key: str) -> Any:
        if isinstance(cand, dict) and is_measured(cand.get(num_key)) and is_measured(cand.get(den_key)):
            return {"num": cand[num_key], "den": cand[den_key], "ratio": cand.get(num_key) / cand[den_key]
                    if cand[den_key] else NOT_YET_MEASURED}
        return NOT_YET_MEASURED

    return {
        "interval_candidate_recall": frac("interval_num", "interval_den"),
        "complete_set_positive_query_recall": frac("complete_set_num", "complete_set_den"),
        "empty_set_rejection_f1": empty if is_measured(empty) else NOT_YET_MEASURED,
        "system_undetermined_rate": (rc.get("undetermined_rate")
                                     if isinstance(rc, dict) and is_measured(rc.get("undetermined_rate"))
                                     else NOT_YET_MEASURED),
    }


def render_text(panel: dict) -> str:
    """Render the §22.4 UI text block (verbatim structure)."""
    h = panel.get("headline", {})

    def frac_str(key: str) -> str:
        v = h.get(key)
        if isinstance(v, dict) and is_measured(v.get("num")) and is_measured(v.get("den")):
            return f"{v['num']}/{v['den']}"
        return "not yet measured"

    def scalar_str(key: str) -> str:
        v = h.get(key)
        return f"{v:.3f}" if is_measured(v) else "not yet measured"

    lines = [
        f"Held-out benchmark, config {panel.get('config_hash', 'not yet measured')}, "
        f"evaluated {panel.get('evaluated_date', 'not yet measured')}",
        f"Interval candidate recall: {frac_str('interval_candidate_recall')}",
        f"Complete-set positive-query recall: {frac_str('complete_set_positive_query_recall')}",
        f"Empty-set rejection F1: {scalar_str('empty_set_rejection_f1')}",
        f"System undetermined rate: {scalar_str('system_undetermined_rate')}",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def any_measured(panel: dict) -> bool:
    """True iff at least one configuration reports a measured status."""
    return any(sec.get("status") == "measured" for sec in panel.get("configurations", {}).values())
