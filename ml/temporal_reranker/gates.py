"""Measured release-gate evaluation.  Missing measurements never pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ShipGateReport:
    status: str
    passed: bool
    blockers: tuple[str, ...]
    qualifying_improvement: str | None
    measured: Mapping[str, float | bool | str | None]


REQUIRED_METRICS = frozenset(
    {
        "baseline_temporal_set_f1",
        "reranker_temporal_set_f1",
        "baseline_vlm_calls",
        "reranker_vlm_calls",
        "baseline_candidate_recall",
        "reranker_candidate_recall",
        "baseline_rejection_f1",
        "reranker_rejection_f1",
        "baseline_required_macro_f1",
        "reranker_required_macro_f1",
        "baseline_atom_support_f1",
        "reranker_atom_support_f1",
        "baseline_complete_set_recall",
        "reranker_complete_set_recall",
        "reranker_latency_s",
        "fallback_exercised",
    }
)


def evaluate_ship_gate(
    metrics: Mapping[str, Any],
    *,
    rejection_material_tolerance: float | None,
) -> ShipGateReport:
    missing = sorted(name for name in REQUIRED_METRICS if metrics.get(name) is None)
    blockers: list[str] = []
    if missing:
        blockers.append("missing held-out measurements: " + ", ".join(missing))
    if rejection_material_tolerance is None:
        blockers.append(
            "rejection material-decrease tolerance is not declared by the operating point"
        )
    elif rejection_material_tolerance < 0:
        blockers.append("rejection material-decrease tolerance must be nonnegative")
    if blockers:
        return ShipGateReport(
            status="not_measured",
            passed=False,
            blockers=tuple(blockers),
            qualifying_improvement=None,
            measured=dict(metrics),
        )

    numeric = {name: float(metrics[name]) for name in REQUIRED_METRICS if name != "fallback_exercised"}
    f1_gain = numeric["reranker_temporal_set_f1"] - numeric["baseline_temporal_set_f1"]
    same_recall = (
        numeric["reranker_candidate_recall"] >= numeric["baseline_candidate_recall"]
    )
    call_reduction = (
        0.0
        if numeric["baseline_vlm_calls"] <= 0
        else 1.0 - numeric["reranker_vlm_calls"] / numeric["baseline_vlm_calls"]
    )
    improvement = None
    if f1_gain >= 0.03:
        improvement = "temporal_set_f1"
    elif call_reduction >= 0.25 and same_recall:
        improvement = "vlm_call_reduction_at_same_candidate_recall"
    else:
        blockers.append(
            "neither +3 point temporal set F1 nor 25% fewer VLM calls at the same candidate recall"
        )

    assert rejection_material_tolerance is not None
    if (
        numeric["reranker_rejection_f1"]
        < numeric["baseline_rejection_f1"] - rejection_material_tolerance
    ):
        blockers.append("rejection F1 decreased materially")
    if numeric["reranker_required_macro_f1"] < numeric["baseline_required_macro_f1"]:
        blockers.append("required-condition macro-F1 decreased")
    if numeric["reranker_atom_support_f1"] < numeric["baseline_atom_support_f1"]:
        blockers.append("per-atom support decreased")
    if numeric["reranker_complete_set_recall"] < numeric["baseline_complete_set_recall"]:
        blockers.append("complete-set recall decreased")
    if numeric["reranker_latency_s"] >= 1.0:
        blockers.append("reranker inference adds one second or more per query")
    if not bool(metrics["fallback_exercised"]):
        blockers.append("baseline RRF fallback was not exercised")

    return ShipGateReport(
        status="passed" if not blockers else "failed",
        passed=not blockers,
        blockers=tuple(blockers),
        qualifying_improvement=improvement,
        measured=dict(metrics),
    )
