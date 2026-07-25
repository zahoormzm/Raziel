"""Evaluation configurations, ablation catalogue, and the prediction contract.

Defines the six evaluation interfaces (B1, B2, B3, FULL, FULL+GRAPH, FULL+STR)
under **one declared budget** (§22.2) and all thirteen ablations (§22.3). This
module NEVER produces model outputs. It declares *what* each configuration runs
and *how* to score predictions that the real system (owned by Members 1-3)
emits. If predictions are absent for a configuration, every metric stays
``not_yet_measured`` — the code refuses to invent results.

Prediction contract (produced by the system under test, consumed here)::

    {
      "config": "FULL",
      "config_hash": "<operating-point hash>",
      "produced_by": "<owner/run id>",
      "budget": { ... same declared budget for every config ... },
      "predictions": {
        "<family_id>": {
          "candidates":       [interval, ...],   # for candidate recall
          "graph_candidates": [interval, ...],   # optional (FULL+GRAPH)
          "ordered_clusters": [interval, ...],   # verification order (budget recall)
          "matches":          [interval, ...],   # verified matches -> temporal set / boundary
          "returned_empty":   true|false,        # for empty-set rejection
          "atom_predictions": {"<atom_id>": {"state": "...", "reason": "..."}},
          "clusters":         <int>,
          "vlm_calls":        <int>,
          "retrieval_latency_s":    <float>,
          "verification_latency_s": <float>,
          "end_to_end_latency_s":   <float>,
          "coverage": {"embedded_ticks": n, "expected_ticks": m,
                       "anchor_qualifying": a, "anchor_retained": b,
                       "episode_cap_bound": false,
                       "clusters_verified": c, "clusters_total": d},
          "logic_predictions": [{"group_id","operator","pred_outcome","under_stress"}]
        }
      }
    }

An ``interval`` is ``{"video_id","camera_id","t0","t1"}``.

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §22.2, §22.3, §16.6, §26.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import metrics as M
from .schema import NOT_YET_MEASURED, is_measured

# --------------------------------------------------------------------------- #
# One declared budget shared by every configuration (§22.2)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Budget:
    """The single declared candidate/verification budget applied to all configs."""
    candidate_clusters_max: int = 32
    verification_clusters_max: int = 24
    verification_seconds_max: float = 60.0
    declared: bool = True


DEFAULT_DECLARED_BUDGET = Budget()


# --------------------------------------------------------------------------- #
# Lane vocabulary
# --------------------------------------------------------------------------- #

LANES = (
    "whole_query_retrieval",
    "atom_union_retrieval",
    "clip_lane",              # gated (§12.2)
    "motion_densification",   # gated (§11.2)
    "temporal_assembly",
    "whole_query_verifier",
    "constraint_verifier",
    "tracklet_graph",         # gated (§12.4)
    "bounded_logic",          # gated (§13.2)
    "temporal_reranker",      # gated, ship only after held-out gain (§16.6)
    "grounding",              # gated (§18)
)


@dataclass(frozen=True)
class EvalConfig:
    name: str
    description: str
    lanes: frozenset
    requires_trained_model: bool = False
    budget: Budget = DEFAULT_DECLARED_BUDGET


BASELINES: dict[str, EvalConfig] = {
    "B1": EvalConfig(
        name="B1",
        description="Whole-query SigLIP2 frame retrieval only.",
        lanes=frozenset({"whole_query_retrieval"}),
    ),
    "B2": EvalConfig(
        name="B2",
        description="Atom-union retrieval without per-constraint verification.",
        lanes=frozenset({"whole_query_retrieval", "atom_union_retrieval"}),
    ),
    "B3": EvalConfig(
        name="B3",
        description="B2 plus one whole-query VLM yes/no per candidate.",
        lanes=frozenset({"whole_query_retrieval", "atom_union_retrieval",
                         "whole_query_verifier"}),
    ),
    "FULL": EvalConfig(
        name="FULL",
        description="Atom union + optional clip lane + temporal assembly + constraint verifier.",
        lanes=frozenset({"whole_query_retrieval", "atom_union_retrieval", "clip_lane",
                         "temporal_assembly", "constraint_verifier"}),
    ),
    "FULL+GRAPH": EvalConfig(
        name="FULL+GRAPH",
        description="FULL + tracklet/temporal-graph candidates and bounded-logic execution.",
        lanes=frozenset({"whole_query_retrieval", "atom_union_retrieval", "clip_lane",
                         "temporal_assembly", "constraint_verifier",
                         "tracklet_graph", "bounded_logic"}),
    ),
    "FULL+STR": EvalConfig(
        name="FULL+STR",
        description="FULL with the trained Temporal Evidence Reranker (only if trained).",
        lanes=frozenset({"whole_query_retrieval", "atom_union_retrieval", "clip_lane",
                         "temporal_assembly", "constraint_verifier", "temporal_reranker"}),
        requires_trained_model=True,
    ),
}

BASELINE_ORDER = ["B1", "B2", "B3", "FULL", "FULL+GRAPH", "FULL+STR"]


# --------------------------------------------------------------------------- #
# Ablations (§22.3), in the plan's priority order
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Ablation:
    ablation_id: int
    name: str
    arm_a: str
    arm_b: str
    metric_focus: str


ABLATIONS: list[Ablation] = [
    Ablation(1, "Window aggregation", "max | mean | top-20%-mean | persistence",
             "(swept together)", "candidate_recall"),
    Ablation(2, "Retrieval breadth", "whole-query", "atom-union", "candidate_recall"),
    Ablation(3, "Sampling", "base sampling", "motion densification", "action_order_recall"),
    Ablation(4, "Clip lane", "frame lane", "frame + clip lane", "action_order_recall"),
    Ablation(5, "Assembly", "no assembly", "temporal assembly", "temporal_set_f1"),
    Ablation(6, "Verifier", "whole-query verifier", "structured constraint verifier",
             "required_condition_macro_f1"),
    Ablation(7, "Recovery", "structured single call", "focused per-atom recovery",
             "required_condition_macro_f1"),
    Ablation(8, "Track/graph union", "semantic-only", "semantic + track/graph union",
             "candidate_recall"),
    Ablation(9, "Grounding source", "candidate-only grounding", "archive tracklet memory",
             "graph_pattern_candidate_recall"),
    Ablation(10, "Bounded logic", "disabled", "enabled (supported subset)",
             "bounded_logic_correctness"),
    Ablation(11, "Ordering", "RRF ordering", "trained temporal reranker",
             "vlm_calls_per_query"),
    Ablation(12, "Reranker heads", "relevance-only", "relevance + per-atom heads",
             "required_condition_macro_f1"),
    Ablation(13, "Encoder", "base SigLIP2", "larger SigLIP2", "candidate_recall"),
]


# --------------------------------------------------------------------------- #
# Prediction loading (never fabricated)
# --------------------------------------------------------------------------- #

class PredictionsUnavailable(Exception):
    """Raised when a caller asks to score a config that has no supplied predictions."""


@dataclass
class PredictionsBundle:
    config: str
    config_hash: Optional[str]
    produced_by: Optional[str]
    predictions: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, obj: dict) -> "PredictionsBundle":
        return cls(
            config=obj.get("config", "?"),
            config_hash=obj.get("config_hash"),
            produced_by=obj.get("produced_by"),
            predictions=obj.get("predictions", {}),
        )


def _gts_for_family(family: dict) -> list[dict]:
    return family.get("ground_truth", {}).get("intervals", {}).get("intervals", [])


def _is_empty_family(family: dict) -> bool:
    return family.get("ground_truth", {}).get("intervals", {}).get("cardinality") == "zero"


def _is_action_order_family(family: dict) -> bool:
    tags = set(family.get("capability_tags", []))
    return bool(tags & {"action", "temporal_order"})


def _atom_gt_map(family: dict) -> dict[str, str]:
    out = {}
    for a in family.get("ground_truth", {}).get("atoms_relations", {}).get("atoms", []):
        if "gt_state" in a:  # empty-set families have atoms with no target state to score
            out[a["atom_id"]] = a["gt_state"]
    return out


# --------------------------------------------------------------------------- #
# Scoring a configuration from supplied predictions
# --------------------------------------------------------------------------- #

def compute_config_metrics(families: list[dict], bundle: Optional[PredictionsBundle],
                           config: EvalConfig, iou_threshold: float = 0.5,
                           candidate_iou_threshold: float = 0.0) -> dict:
    """Score one configuration.

    Returns a metrics section whose every field is a real number OR
    ``not_yet_measured``. When ``bundle`` is ``None`` (no supplied predictions),
    every field is ``not_yet_measured`` — this function does not invent results.
    """
    section: dict[str, Any] = {"config": config.name, "description": config.description}

    if bundle is None:
        section["status"] = NOT_YET_MEASURED
        section["config_hash"] = NOT_YET_MEASURED
        return _fill_not_yet_measured(section)

    section["status"] = "measured"
    section["config_hash"] = bundle.config_hash or NOT_YET_MEASURED
    section["produced_by"] = bundle.produced_by or NOT_YET_MEASURED
    preds = bundle.predictions
    budget = config.budget

    # ---- candidate recall (§22.1(1)) ----
    all_cand_hits = []       # per positive family: fully recalled?
    interval_terms = []      # (recalled_count, total) accumulation
    action_queries = []
    complete_queries = []
    budget_queries = []
    for fam in families:
        gts = _gts_for_family(fam)
        if not gts:
            continue
        rec = preds.get(fam["family_id"], {})
        cands = rec.get("candidates", [])
        interval_terms.append((
            sum(1 for g in gts if M._is_recalled(g, cands, candidate_iou_threshold)),
            len(gts),
        ))
        complete_queries.append({"candidates": cands, "gts": gts})
        budget_queries.append({
            "ordered": rec.get("ordered_clusters", cands), "gts": gts,
        })
        if _is_action_order_family(fam):
            action_queries.append({"candidates": cands, "gts": gts})

    interval_num = sum(h for h, _ in interval_terms)
    interval_den = sum(t for _, t in interval_terms)
    complete_positive = [q for q in complete_queries if q["gts"]]
    complete_full = sum(
        1 for q in complete_positive
        if all(M._is_recalled(g, q["candidates"], candidate_iou_threshold) for g in q["gts"])
    )
    section["candidate_recall"] = {
        "interval": _weighted_recall(interval_terms),
        "interval_num": interval_num,
        "interval_den": interval_den,
        "complete_set": _safe(M.complete_set_query_recall(complete_queries, candidate_iou_threshold)),
        "complete_set_num": complete_full,
        "complete_set_den": len(complete_positive),
        "action_order": _safe(M.complete_set_query_recall(action_queries, candidate_iou_threshold))
                        if action_queries else NOT_YET_MEASURED,
        "within_budget": _weighted_recall([
            (sum(1 for g in q["gts"]
                 if M._is_recalled(g, list(q["ordered"])[:budget.verification_clusters_max],
                                   candidate_iou_threshold)),
             len(q["gts"]))
            for q in budget_queries
        ]),
    }

    # ---- temporal set PRF (§22.1(2)) ----
    ts_queries = [{"preds": preds.get(f["family_id"], {}).get("matches", []),
                   "gts": _gts_for_family(f)}
                  for f in families if _gts_for_family(f)]
    prf = M.temporal_set_prf_micro(ts_queries, iou_threshold)
    section["temporal_set"] = ({
        "precision": prf.precision, "recall": prf.recall, "f1": prf.f1,
        "tp": prf.tp, "fp": prf.fp, "fn": prf.fn, "t_iou_threshold": iou_threshold,
    } if prf else NOT_YET_MEASURED)

    # ---- empty-set rejection F1 (§22.1(3)) ----
    rej_families = [{"is_empty": _is_empty_family(f),
                     "returned_empty": bool(preds.get(f["family_id"], {}).get("returned_empty", False))}
                    for f in families]
    rej = M.empty_set_rejection_f1(rej_families)
    section["empty_set_rejection_f1"] = (rej.prf.f1 if rej else NOT_YET_MEASURED)

    # ---- required-condition macro-F1 (§22.1(4)) ----
    items = []
    for fam in families:
        gt_map = _atom_gt_map(fam)
        atom_preds = preds.get(fam["family_id"], {}).get("atom_predictions", {})
        for atom_id, gt_state in gt_map.items():
            p = atom_preds.get(atom_id)
            if p is None:
                continue
            items.append({"gt": gt_state, "pred": p.get("state"),
                          "reason": p.get("reason", "unspecified")})
    macro = M.required_condition_macro_f1(items) if items else None
    section["required_condition"] = ({
        "macro_f1": macro.macro_f1,
        "per_class": {c: r.f1 for c, r in macro.per_class.items()},
        "undetermined_rate": macro.undetermined_rate,
        "undetermined_by_reason": macro.undetermined_by_reason,
        "n": macro.n,
    } if macro else NOT_YET_MEASURED)

    # ---- boundary error (§22.1(5)) ----
    section["boundary_error"] = _boundary_section(families, preds, iou_threshold)

    # ---- latency / efficiency (§22.1(6)) ----
    section["latency_efficiency"] = _latency_section(families, preds)

    # ---- coverage (§22.1(7)) ----
    section["coverage"] = _coverage_section(families, preds)

    # ---- optional lanes (only if this config enables them) ----
    if "bounded_logic" in config.lanes:
        section["bounded_logic"] = _bounded_logic_section(families, preds)
    if "tracklet_graph" in config.lanes:
        section["track_graph"] = {"note": "populate from supplied track/graph predictions"}

    return section


# --------------------------------------------------------------------------- #
# Section helpers
# --------------------------------------------------------------------------- #

def _safe(value) -> Any:
    return value if is_measured(value) else (value if value == NOT_YET_MEASURED else NOT_YET_MEASURED)


def _weighted_recall(terms: list[tuple[int, int]]) -> Any:
    total = sum(t for _, t in terms)
    if total == 0:
        return NOT_YET_MEASURED
    return sum(h for h, _ in terms) / total


def _boundary_section(families, preds, iou_threshold) -> Any:
    pairs = []
    for fam in families:
        gts = _gts_for_family(fam)
        matches = preds.get(fam["family_id"], {}).get("matches", [])
        if not gts or not matches:
            continue
        mr = M.one_to_one_match(matches, gts, iou_threshold)
        for m in mr.matches:
            pairs.append((matches[m.pred_index], gts[m.gt_index]))
    be = M.boundary_error(pairs)
    if be is None:
        return NOT_YET_MEASURED
    return {
        "median_start_error_s": be.median_start_error_s,
        "median_end_error_s": be.median_end_error_s,
        "median_combined_error_s": be.median_combined_error_s,
        "n": be.n,
    }


def _latency_section(families, preds) -> dict:
    retr, verif, e2e, calls, clusters = [], [], [], [], []
    for fam in families:
        rec = preds.get(fam["family_id"], {})
        for key, bucket in (("retrieval_latency_s", retr),
                            ("verification_latency_s", verif),
                            ("end_to_end_latency_s", e2e)):
            if is_measured(rec.get(key)):
                bucket.append(rec[key])
        if is_measured(rec.get("vlm_calls")):
            calls.append(rec["vlm_calls"])
        if is_measured(rec.get("clusters")):
            clusters.append(rec["clusters"])

    def stats(samples):
        s = M.latency_stats(samples)
        return {"median_s": s.median_s, "p95_s": s.p95_s, "n": s.n} if s else NOT_YET_MEASURED

    return {
        "retrieval": stats(retr),
        "verification": stats(verif),
        "end_to_end": stats(e2e),
        "vlm_calls_per_query": _safe(M.mean(calls)) if calls else NOT_YET_MEASURED,
        "clusters_per_query": _safe(M.mean(clusters)) if clusters else NOT_YET_MEASURED,
        "indexing_throughput_ticks_per_s": NOT_YET_MEASURED,  # supplied by ingestion benchmark (Member 1)
    }


def _coverage_section(families, preds) -> Any:
    emb = exp = cv = ct = 0
    aq = ar = 0
    cap_bound = False
    seen = False
    for fam in families:
        cov = preds.get(fam["family_id"], {}).get("coverage")
        if not cov:
            continue
        seen = True
        emb += cov.get("embedded_ticks", 0)
        exp += cov.get("expected_ticks", 0)
        cv += cov.get("clusters_verified", 0)
        ct += cov.get("clusters_total", 0)
        aq += cov.get("anchor_qualifying", 0)
        ar += cov.get("anchor_retained", 0)
        cap_bound = cap_bound or bool(cov.get("episode_cap_bound", False))
    if not seen:
        return NOT_YET_MEASURED
    asm = M.assembly_completeness(aq, ar, cap_bound)
    return {
        "sampled_tick_coverage": _safe(M.sampled_tick_coverage(emb, exp)),
        "assembly_retained_ratio": _safe(asm.retained_ratio),
        "assembly_complete": asm.complete,
        "verification_completeness": _safe(M.verification_completeness(cv, ct)),
    }


def _bounded_logic_section(families, preds) -> Any:
    items = []
    for fam in families:
        gt_groups = {g["group_id"]: g for g in
                     fam.get("ground_truth", {}).get("atoms_relations", {}).get("logic_groups", [])}
        # track_logic ground truth carries the authoritative outcomes for count/absence
        for lp in preds.get(fam["family_id"], {}).get("logic_predictions", []):
            gid = lp.get("group_id")
            gt_outcome = _logic_gt_outcome(fam, gid, gt_groups.get(gid))
            if gt_outcome is None:
                continue
            items.append({
                "operator": lp.get("operator"),
                "gt_outcome": gt_outcome,
                "pred_outcome": lp.get("pred_outcome"),
                "under_stress": bool(lp.get("under_stress", False)),
            })
    bl = M.bounded_logic_accuracy_by_operator(items) if items else None
    if bl is None:
        return NOT_YET_MEASURED
    return {
        "accuracy_by_operator": bl.accuracy_by_operator,
        "unresolved_rate_under_stress": _safe(bl.unresolved_rate_under_stress),
        "false_clean_negative_count": bl.false_clean_negative_count,
        "n": bl.n,
    }


def _logic_gt_outcome(family: dict, group_id: str, logic_group: Optional[dict]) -> Any:
    """Resolve the ground-truth outcome for a logic group from track_logic if present,
    else from the logic_group's gt_outcome."""
    tl = family.get("ground_truth", {}).get("track_logic", {})
    for c in tl.get("count_gt", []):
        if c.get("group_id") == group_id:
            return c.get("expected_outcome")
    for vn in tl.get("visible_none_gt", []):
        if vn.get("group_id") == group_id:
            return vn.get("expected_outcome")
    for d in tl.get("disjunction_gt", []):
        if d.get("group_id") == group_id:
            return d.get("expected_outcome")
    if logic_group is not None:
        return logic_group.get("gt_outcome")
    return None


def _fill_not_yet_measured(section: dict) -> dict:
    section.update({
        "candidate_recall": NOT_YET_MEASURED,
        "temporal_set": NOT_YET_MEASURED,
        "empty_set_rejection_f1": NOT_YET_MEASURED,
        "required_condition": NOT_YET_MEASURED,
        "boundary_error": NOT_YET_MEASURED,
        "latency_efficiency": NOT_YET_MEASURED,
        "coverage": NOT_YET_MEASURED,
    })
    return section
