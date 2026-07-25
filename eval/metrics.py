"""Every §22.1 primary-dashboard metric, plus the optional §22.1(8-10) lanes.

Pure functions over ground truth and *supplied* predictions. Nothing here
produces model outputs — predictions are injected by the caller (see
``eval.baselines`` / ``eval.run_eval``). Metrics return ``None`` when there is no
data to measure; the benchmark panel renders that as ``not_yet_measured`` rather
than inventing a number (§22.4).

Declared choices (documented so they are auditable, not silently defaulted):
  * Temporal set matching is **greedy one-to-one by descending t-IoU** with the
    declared threshold (initially 0.5, §22.1(2)); ties break by earlier ground
    truth then earlier prediction index — deterministic.
  * Candidate recall counts a ground-truth interval as recalled when the best
    candidate t-IoU ``>= iou_threshold`` (or, for ``iou_threshold==0``, any
    positive overlap).
  * ``p95`` uses numpy-style linear interpolation between order statistics.
  * ``combined`` boundary error is the median over matched pairs of
    ``(|Δstart| + |Δend|) / 2``.

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §22.1, §14.4, §15.2, §17.4, §9.3.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from .schema import EVIDENCE_STATES, GROUND_TRUTH_STATES, LOGIC_OPERATORS

Number = float


# --------------------------------------------------------------------------- #
# Interval geometry
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Interval:
    video_id: str
    camera_id: str
    t0: float
    t1: float

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    @classmethod
    def coerce(cls, obj: "Interval | dict") -> "Interval":
        if isinstance(obj, Interval):
            return obj
        return cls(
            video_id=obj["video_id"],
            camera_id=obj["camera_id"],
            t0=float(obj["t0"]),
            t1=float(obj["t1"]),
        )


def _same_track(a: Interval, b: Interval) -> bool:
    # Same video AND camera. The system never joins across cameras or discontinuous
    # gaps (§3.3, §15.1), so a cross-camera pair can never match.
    return a.video_id == b.video_id and a.camera_id == b.camera_id


def t_iou(a: "Interval | dict", b: "Interval | dict") -> float:
    """Temporal IoU on the same video/camera; 0.0 across cameras/videos."""
    a, b = Interval.coerce(a), Interval.coerce(b)
    if not _same_track(a, b):
        return 0.0
    inter = max(0.0, min(a.t1, b.t1) - max(a.t0, b.t0))
    union = a.duration + b.duration - inter
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------- #
# One-to-one matching (§22.1(2))
# --------------------------------------------------------------------------- #

@dataclass
class Match:
    pred_index: int
    gt_index: int
    iou: float


@dataclass
class MatchResult:
    matches: list[Match]
    unmatched_pred: list[int]
    unmatched_gt: list[int]


def one_to_one_match(preds: Sequence, gts: Sequence, threshold: float = 0.5) -> MatchResult:
    """Greedy one-to-one matching by descending t-IoU above ``threshold``.

    Deterministic tie-break: higher IoU, then lower gt index, then lower pred index.
    """
    pairs: list[tuple[float, int, int]] = []
    for pi, p in enumerate(preds):
        for gi, g in enumerate(gts):
            iou = t_iou(p, g)
            if iou >= threshold and iou > 0:
                pairs.append((iou, gi, pi))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))

    used_pred: set[int] = set()
    used_gt: set[int] = set()
    matches: list[Match] = []
    for iou, gi, pi in pairs:
        if pi in used_pred or gi in used_gt:
            continue
        used_pred.add(pi)
        used_gt.add(gi)
        matches.append(Match(pred_index=pi, gt_index=gi, iou=iou))

    matches.sort(key=lambda m: m.gt_index)
    return MatchResult(
        matches=matches,
        unmatched_pred=[i for i in range(len(preds)) if i not in used_pred],
        unmatched_gt=[i for i in range(len(gts)) if i not in used_gt],
    )


# --------------------------------------------------------------------------- #
# Candidate recall (§22.1(1))
# --------------------------------------------------------------------------- #

def _is_recalled(gt, candidates: Sequence, iou_threshold: float) -> bool:
    best = max((t_iou(c, gt) for c in candidates), default=0.0)
    return best >= iou_threshold if iou_threshold > 0 else best > 0.0


def interval_candidate_recall(candidates: Sequence, gts: Sequence,
                              iou_threshold: float = 0.0) -> Optional[float]:
    """Fraction of ground-truth intervals surfaced by at least one candidate.

    Retrieval is recall-first (§14.4); a GT interval is recalled if a candidate
    window overlaps it enough that the verifier would see it.
    """
    if not gts:
        return None
    hit = sum(1 for g in gts if _is_recalled(g, candidates, iou_threshold))
    return hit / len(gts)


def complete_set_query_recall(queries: Sequence[dict],
                              iou_threshold: float = 0.0) -> Optional[float]:
    """Fraction of positive queries whose EVERY ground-truth interval is recalled.

    ``queries`` items: ``{"candidates": [...], "gts": [...]}``. Empty-set (zero GT)
    queries are excluded from this positive-query metric.
    """
    positives = [q for q in queries if q.get("gts")]
    if not positives:
        return None
    full = 0
    for q in positives:
        gts, cands = q["gts"], q.get("candidates", [])
        if all(_is_recalled(g, cands, iou_threshold) for g in gts):
            full += 1
    return full / len(positives)


def recall_within_budget(ordered_clusters: Sequence, gts: Sequence, budget_k: int,
                         iou_threshold: float = 0.0) -> Optional[float]:
    """Candidate recall using only the first ``budget_k`` verification-ordered clusters.

    A budget-truncated search is never a clean no-match (§4.2/§14.4); this measures
    how much recall survives the declared verification budget.
    """
    if not gts:
        return None
    return interval_candidate_recall(list(ordered_clusters)[:budget_k], gts, iou_threshold)


# --------------------------------------------------------------------------- #
# Temporal set precision / recall / F1 (§22.1(2))
# --------------------------------------------------------------------------- #

@dataclass
class PRF:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


def _prf(tp: int, fp: int, fn: int) -> PRF:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return PRF(precision, recall, f1, tp, fp, fn)


def temporal_set_prf(preds: Sequence, gts: Sequence, threshold: float = 0.5) -> PRF:
    """Per-query temporal set PRF via one-to-one t-IoU matching."""
    result = one_to_one_match(preds, gts, threshold)
    tp = len(result.matches)
    return _prf(tp, len(preds) - tp, len(gts) - tp)


def temporal_set_prf_micro(queries: Sequence[dict], threshold: float = 0.5) -> Optional[PRF]:
    """Micro-averaged temporal set PRF over queries (sum TP/FP/FN, then divide).

    ``queries`` items: ``{"preds": [...], "gts": [...]}``.
    """
    if not queries:
        return None
    tp = fp = fn = 0
    for q in queries:
        r = one_to_one_match(q.get("preds", []), q.get("gts", []), threshold)
        m = len(r.matches)
        tp += m
        fp += len(q.get("preds", [])) - m
        fn += len(q.get("gts", [])) - m
    return _prf(tp, fp, fn)


# --------------------------------------------------------------------------- #
# Empty-set rejection F1 (§22.1(3))
# --------------------------------------------------------------------------- #

@dataclass
class RejectionResult:
    prf: PRF
    n_empty: int
    n_nonempty: int


def empty_set_rejection_f1(families: Sequence[dict]) -> Optional[RejectionResult]:
    """F1 of correctly returning no-verified-match on reviewed absent families.

    Positive event = "the correct answer is empty-set".
      TP: family empty AND system returned no verified match.
      FP: family NOT empty BUT system returned no verified match (wrong rejection).
      FN: family empty BUT system returned a verified match (hallucinated match).

    ``families`` items: ``{"is_empty": bool, "returned_empty": bool}``.
    """
    if not families:
        return None
    tp = fp = fn = 0
    n_empty = n_nonempty = 0
    for f in families:
        is_empty = bool(f["is_empty"])
        returned_empty = bool(f["returned_empty"])
        n_empty += is_empty
        n_nonempty += (not is_empty)
        if is_empty and returned_empty:
            tp += 1
        elif (not is_empty) and returned_empty:
            fp += 1
        elif is_empty and (not returned_empty):
            fn += 1
    return RejectionResult(prf=_prf(tp, fp, fn), n_empty=n_empty, n_nonempty=n_nonempty)


# --------------------------------------------------------------------------- #
# Required-condition semantic macro-F1 (§22.1(4))
# --------------------------------------------------------------------------- #

@dataclass
class MacroF1Result:
    macro_f1: float
    per_class: dict[str, PRF]
    classes_averaged: list[str]
    undetermined_rate: float
    undetermined_by_reason: dict[str, int]
    support: dict[str, int]
    n: int


def required_condition_macro_f1(items: Sequence[dict]) -> Optional[MacroF1Result]:
    """Macro-F1 over ground-truth classes supported/contradicted/unobservable.

    ``undetermined`` predictions count as unresolved/incorrect: they never earn a
    true positive for any class and are reported separately by reason (§22.1(4)).

    ``items`` entries: ``{"gt": <one of GROUND_TRUTH_STATES>,
    "pred": <one of EVIDENCE_STATES>, "reason": <optional str>}``.
    """
    if not items:
        return None
    classes = GROUND_TRUTH_STATES
    tp = {c: 0 for c in classes}
    fp = {c: 0 for c in classes}
    fn = {c: 0 for c in classes}
    support = {c: 0 for c in classes}
    undetermined = 0
    by_reason: dict[str, int] = {}

    for it in items:
        gt = it["gt"]
        pred = it["pred"]
        if gt not in classes:
            raise ValueError(f"ground-truth state {gt!r} is not a valid label {classes}")
        if pred not in EVIDENCE_STATES:
            raise ValueError(f"prediction state {pred!r} not in {EVIDENCE_STATES}")
        support[gt] += 1

        if pred == "undetermined":
            undetermined += 1
            reason = it.get("reason", "unspecified")
            by_reason[reason] = by_reason.get(reason, 0) + 1
            fn[gt] += 1  # missed the true class; not a positive for any class
            continue

        if pred == gt:
            tp[gt] += 1
        else:
            fp[pred] += 1
            fn[gt] += 1

    per_class = {c: _prf(tp[c], fp[c], fn[c]) for c in classes}
    # Macro-average over classes PRESENT in the ground truth (support > 0). A class
    # with no ground-truth examples has undefined recall; excluding it does not hide
    # errors, because a misprediction still lowers the true class's recall (and the
    # mispredicted class's precision is folded into that class only when it has
    # support). On a full dataset all three classes are present.
    present = [c for c in classes if support[c] > 0]
    macro = (sum(per_class[c].f1 for c in present) / len(present)) if present else 0.0
    return MacroF1Result(
        macro_f1=macro,
        per_class=per_class,
        classes_averaged=present,
        undetermined_rate=undetermined / len(items),
        undetermined_by_reason=dict(sorted(by_reason.items())),
        support=support,
        n=len(items),
    )


# --------------------------------------------------------------------------- #
# Boundary error (§22.1(5))
# --------------------------------------------------------------------------- #

@dataclass
class BoundaryError:
    median_start_error_s: float
    median_end_error_s: float
    median_combined_error_s: float
    n: int


def boundary_error(pairs: Sequence[tuple]) -> Optional[BoundaryError]:
    """Median absolute start / end / combined boundary error over matched pairs.

    ``pairs``: iterable of ``(pred_interval, gt_interval)`` already matched.
    """
    if not pairs:
        return None
    starts, ends, combined = [], [], []
    for pred, gt in pairs:
        p, g = Interval.coerce(pred), Interval.coerce(gt)
        ds, de = abs(p.t0 - g.t0), abs(p.t1 - g.t1)
        starts.append(ds)
        ends.append(de)
        combined.append((ds + de) / 2.0)
    return BoundaryError(
        median_start_error_s=statistics.median(starts),
        median_end_error_s=statistics.median(ends),
        median_combined_error_s=statistics.median(combined),
        n=len(pairs),
    )


# --------------------------------------------------------------------------- #
# Latency and efficiency (§22.1(6), §9.3)
# --------------------------------------------------------------------------- #

def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """numpy-style linear-interpolation percentile. ``q`` in [0, 1]."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    rank = q * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


@dataclass
class LatencyStats:
    median_s: Optional[float]
    p95_s: Optional[float]
    n: int


def latency_stats(samples: Sequence[float]) -> Optional[LatencyStats]:
    """Median and p95 latency. Warm-run counts (§9.3) are the caller's concern."""
    if not samples:
        return None
    return LatencyStats(
        median_s=percentile(samples, 0.5),
        p95_s=percentile(samples, 0.95),
        n=len(samples),
    )


def mean(values: Sequence[float]) -> Optional[float]:
    values = list(values)
    return (sum(values) / len(values)) if values else None


def indexing_throughput(items_processed: float, seconds: float) -> Optional[float]:
    """Ticks (or frames) per second."""
    if seconds is None or seconds <= 0:
        return None
    return items_processed / seconds


# --------------------------------------------------------------------------- #
# Coverage (§22.1(7), §15.2)
# --------------------------------------------------------------------------- #

def _ratio(num: float, denom: float) -> Optional[float]:
    if denom is None or denom <= 0:
        return None
    return num / denom


def sampled_tick_coverage(embedded_ticks: int, expected_ticks: int) -> Optional[float]:
    """Scored coverage = embedded / expected ticks. Decode failures stay in the
    denominator (§11.4) so coverage is honest."""
    return _ratio(embedded_ticks, expected_ticks)


@dataclass
class AssemblyCompleteness:
    retained_ratio: Optional[float]
    complete: bool


def assembly_completeness(anchor_qualifying: int, anchor_retained: int,
                          episode_cap_bound: bool) -> AssemblyCompleteness:
    """Assembly is complete only if every qualifying anchor is retained and no
    episode cap binds (§15.2). Otherwise no clean no-match is allowed."""
    ratio = _ratio(anchor_retained, anchor_qualifying)
    complete = (anchor_retained == anchor_qualifying) and not episode_cap_bound
    return AssemblyCompleteness(retained_ratio=ratio, complete=complete)


def verification_completeness(clusters_verified: int, clusters_total: int) -> Optional[float]:
    return _ratio(clusters_verified, clusters_total)


# --------------------------------------------------------------------------- #
# Optional: spatial evidence quality (§22.1(8))
# --------------------------------------------------------------------------- #

def box_validity_rate(valid_boxes: int, total_boxes: int) -> Optional[float]:
    return _ratio(valid_boxes, total_boxes)


def overlay_failure_rate(failed_overlays: int, total_overlays: int) -> Optional[float]:
    return _ratio(failed_overlays, total_overlays)


# --------------------------------------------------------------------------- #
# Optional: track and graph quality (§22.1(9))
# --------------------------------------------------------------------------- #

def track_fragmentation_rate(fragmented_tracks: int, total_tracks: int) -> Optional[float]:
    return _ratio(fragmented_tracks, total_tracks)


def duplicate_track_rate(duplicate_tracks: int, total_tracks: int) -> Optional[float]:
    return _ratio(duplicate_tracks, total_tracks)


def count_mae(pairs: Sequence[tuple]) -> Optional[float]:
    """Mean absolute error over RESOLVED count pairs ``(pred_count, gt_count)``.

    Pairs where either side is ``'unresolved'`` are excluded — an unresolved count
    is a correct abstention, not a numeric error (§14.3). Callers should also
    report how many were unresolved via ``bounded_logic_accuracy_by_operator``.
    """
    resolved = [(p, g) for p, g in pairs
                if isinstance(p, (int, float)) and not isinstance(p, bool)
                and isinstance(g, (int, float)) and not isinstance(g, bool)]
    if not resolved:
        return None
    return sum(abs(p - g) for p, g in resolved) / len(resolved)


def graph_pattern_candidate_recall(candidates: Sequence, gts: Sequence,
                                   iou_threshold: float = 0.0) -> Optional[float]:
    """Recall of graph-pattern candidates against GT intervals (§22.1(9))."""
    return interval_candidate_recall(candidates, gts, iou_threshold)


def edge_evidence_validity(valid_edges: int, total_edges: int) -> Optional[float]:
    """Fraction of graph edges that resolve to inspectable source frames/PTS (§12.5)."""
    return _ratio(valid_edges, total_edges)


# --------------------------------------------------------------------------- #
# Optional: bounded-logic correctness (§22.1(10))
# --------------------------------------------------------------------------- #

@dataclass
class BoundedLogicResult:
    accuracy_by_operator: dict[str, Optional[float]]
    unresolved_rate_under_stress: Optional[float]
    false_clean_negative_count: int
    n: int


# Outcomes that assert a clean negative / hard positive (NOT an abstention).
_CLEAN_OUTCOMES = {"satisfied", "not_satisfied", "visible_absence_supported",
                   "present_contradicted"}
_ABSTENTION_OUTCOMES = {"unobservable", "unresolved"}


def _is_clean_assertion(outcome: Any) -> bool:
    """A clean assertion is any concrete verdict — a string clean-outcome OR a
    concrete integer count. Asserting an integer count where the truth demands
    ``unresolved`` is itself a false clean-negative (§14.3)."""
    if isinstance(outcome, bool):
        return False
    if isinstance(outcome, int):
        return True
    return outcome in _CLEAN_OUTCOMES


def bounded_logic_accuracy_by_operator(items: Sequence[dict]) -> Optional[BoundedLogicResult]:
    """Exact-outcome accuracy per operator, plus abstention behaviour under stress.

    ``items`` entries::

        {"operator": "any|visible_none|count",
         "gt_outcome": <ground-truth outcome or int>,
         "pred_outcome": <system outcome or int>,
         "under_stress": <bool: occlusion/fragmentation/missing coverage present>}

    ``false_clean_negative_count`` counts the forbidden failure mode: the ground
    truth demands abstention (unobservable/unresolved) yet the system asserted a
    clean negative/positive. This MUST be zero (§22.1(10), §28 risk row).
    """
    if not items:
        return None
    per_op_correct: dict[str, int] = {}
    per_op_total: dict[str, int] = {}
    stress_total = 0
    stress_abstained = 0
    false_clean_negatives = 0

    for it in items:
        op = it["operator"]
        if op not in LOGIC_OPERATORS:
            raise ValueError(f"operator {op!r} not in {LOGIC_OPERATORS}")
        gt = it["gt_outcome"]
        pred = it["pred_outcome"]
        per_op_total[op] = per_op_total.get(op, 0) + 1
        if pred == gt:
            per_op_correct[op] = per_op_correct.get(op, 0) + 1
        if it.get("under_stress"):
            stress_total += 1
            if pred in _ABSTENTION_OUTCOMES:
                stress_abstained += 1
        if gt in _ABSTENTION_OUTCOMES and _is_clean_assertion(pred):
            false_clean_negatives += 1

    accuracy = {
        op: (per_op_correct.get(op, 0) / per_op_total[op]) for op in per_op_total
    }
    return BoundedLogicResult(
        accuracy_by_operator=accuracy,
        unresolved_rate_under_stress=_ratio(stress_abstained, stress_total),
        false_clean_negative_count=false_clean_negatives,
        n=len(items),
    )
