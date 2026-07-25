"""Threshold-first union, recall-preserving RRF ordering, and clustering."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from typing import Any, Iterable, Mapping, Sequence

from query.schema import (
    CandidateClusterData,
    CandidateWindowData,
    EvidenceRefData,
    candidate_window_payload,
    canonical_data,
    coerce_candidate_window,
    stable_identifier,
    unique_preserving_order,
)


def _evidence_union(
    groups: Iterable[Iterable[EvidenceRefData]],
) -> tuple[EvidenceRefData, ...]:
    by_id: dict[str, EvidenceRefData] = {}
    for group in groups:
        for evidence in group:
            by_id.setdefault(evidence.node_id, evidence)
    return tuple(by_id.values())


def threshold_then_union(
    candidates: Iterable[CandidateWindowData | Any],
    *,
    thresholds: Mapping[str, float] | None = None,
) -> tuple[CandidateWindowData, ...]:
    """Union every above-threshold channel window before any ranking.

    A source candidate may contain multiple channel scores.  Only channels at
    or above their configured threshold qualify, but every qualifying interval
    survives regardless of its later RRF score.
    """

    merged: dict[str, CandidateWindowData] = {}
    for value in candidates:
        candidate = coerce_candidate_window(value)
        qualifying = tuple(
            channel
            for channel in candidate.qualifying_channels
            if thresholds is None
            or candidate.channel_scores[channel] >= thresholds.get(channel, float("-inf"))
        )
        if not qualifying:
            continue
        current = merged.get(candidate.candidate_id)
        if current is None:
            merged[candidate.candidate_id] = replace(
                candidate, qualifying_channels=qualifying
            )
            continue
        if (
            current.video_id,
            current.camera_id,
            current.t0,
            current.t1,
        ) != (
            candidate.video_id,
            candidate.camera_id,
            candidate.t0,
            candidate.t1,
        ):
            raise ValueError(
                f"candidate_id collision with different intervals: {candidate.candidate_id}"
            )
        scores = dict(current.channel_scores)
        scores.update(candidate.channel_scores)
        merged[candidate.candidate_id] = CandidateWindowData(
            candidate_id=current.candidate_id,
            video_id=current.video_id,
            camera_id=current.camera_id,
            t0=current.t0,
            t1=current.t1,
            channel_scores=scores,
            qualifying_channels=unique_preserving_order(
                (*current.qualifying_channels, *qualifying)
            ),
            rrf_score=current.rrf_score,
            evidence=_evidence_union((current.evidence, candidate.evidence)),
            atom_ids=unique_preserving_order((*current.atom_ids, *candidate.atom_ids)),
            tracklet_ids=unique_preserving_order(
                (*current.tracklet_ids, *candidate.tracklet_ids)
            ),
            trace_ids=unique_preserving_order(
                (*current.trace_ids, *candidate.trace_ids)
            ),
        )
    return tuple(merged.values())


def union_semantic_and_graph(
    semantic_candidates: Iterable[CandidateWindowData | Any],
    graph_candidates: Iterable[CandidateWindowData | Any],
    *,
    thresholds: Mapping[str, float] | None = None,
) -> tuple[CandidateWindowData, ...]:
    """Graph candidates add to semantic recall and can never suppress it."""

    return threshold_then_union(
        (*tuple(semantic_candidates), *tuple(graph_candidates)),
        thresholds=thresholds,
    )


def reciprocal_rank_order(
    candidates: Sequence[CandidateWindowData | Any], *, k: int = 60
) -> tuple[CandidateWindowData, ...]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    normalised = [coerce_candidate_window(candidate) for candidate in candidates]
    all_ids = {candidate.candidate_id for candidate in normalised}
    channel_ids = sorted(
        {
            channel
            for candidate in normalised
            for channel in candidate.qualifying_channels
        }
    )
    rrf = {candidate.candidate_id: 0.0 for candidate in normalised}
    for channel in channel_ids:
        ranked = sorted(
            (
                candidate
                for candidate in normalised
                if channel in candidate.qualifying_channels
            ),
            key=lambda candidate: (
                -candidate.channel_scores[channel],
                candidate.video_id,
                candidate.t0,
                candidate.candidate_id,
            ),
        )
        for rank, candidate in enumerate(ranked, start=1):
            rrf[candidate.candidate_id] += 1.0 / (k + rank)
    ordered = tuple(
        replace(candidate, rrf_score=rrf[candidate.candidate_id])
        for candidate in sorted(
            normalised,
            key=lambda candidate: (
                -rrf[candidate.candidate_id],
                candidate.video_id,
                candidate.camera_id or "",
                candidate.t0,
                candidate.candidate_id,
            ),
        )
    )
    if {candidate.candidate_id for candidate in ordered} != all_ids:
        raise AssertionError("RRF must not remove candidates")
    return ordered


def cluster_candidates(
    candidates: Sequence[CandidateWindowData | Any],
    *,
    adjacent_gap_s: float = 2.0,
) -> tuple[CandidateClusterData, ...]:
    if adjacent_gap_s < 0:
        raise ValueError("adjacent_gap_s cannot be negative")
    normalised = sorted(
        (coerce_candidate_window(candidate) for candidate in candidates),
        key=lambda item: (
            item.video_id,
            item.camera_id or "",
            item.t0,
            item.t1,
            item.candidate_id,
        ),
    )
    groups: list[list[CandidateWindowData]] = []
    for candidate in normalised:
        if not groups:
            groups.append([candidate])
            continue
        previous_group = groups[-1]
        previous = previous_group[-1]
        group_end = max(item.t1 for item in previous_group)
        same_scope = (
            candidate.video_id == previous.video_id
            and candidate.camera_id == previous.camera_id
        )
        if same_scope and candidate.t0 <= group_end + adjacent_gap_s:
            previous_group.append(candidate)
        else:
            groups.append([candidate])

    clusters: list[CandidateClusterData] = []
    for group in groups:
        first = group[0]
        member_ids = tuple(item.candidate_id for item in group)
        clusters.append(
            CandidateClusterData(
                cluster_id=stable_identifier("cluster", member_ids),
                video_id=first.video_id,
                camera_id=first.camera_id,
                t0=min(item.t0 for item in group),
                t1=max(item.t1 for item in group),
                member_candidate_ids=member_ids,
                priority_score=max(item.rrf_score or 0.0 for item in group),
                evidence=_evidence_union(item.evidence for item in group),
            )
        )
    return tuple(
        sorted(
            clusters,
            key=lambda cluster: (
                -cluster.priority_score,
                cluster.video_id,
                cluster.t0,
            ),
        )
    )


def candidate_cluster_payload(cluster: CandidateClusterData) -> dict[str, Any]:
    return canonical_data(cluster)


def fusion_evaluation_payload(
    windows: Sequence[CandidateWindowData],
    clusters: Sequence[CandidateClusterData],
    *,
    verification_budget_clusters: int | None = None,
) -> dict[str, Any]:
    return {
        "qualifying_windows": len(windows),
        "clusters_total": len(clusters),
        "verification_budget_clusters": verification_budget_clusters,
        "clusters_within_budget": (
            len(clusters)
            if verification_budget_clusters is None
            else min(len(clusters), verification_budget_clusters)
        ),
        "candidate_ids": [window.candidate_id for window in windows],
        "cluster_members": {
            cluster.cluster_id: list(cluster.member_candidate_ids)
            for cluster in clusters
        },
    }


def candidate_set_payload(
    *,
    search_id: str,
    query_plan_version: str,
    channels_run: Sequence[str],
    exact_scoring_completed: bool,
    windows: Sequence[CandidateWindowData],
    clusters: Sequence[CandidateClusterData],
    graph_execution: Mapping[str, Any] | None = None,
    assembly: Mapping[str, Any] | None = None,
    candidate_recall_operating_point: str = "not_yet_measured",
    verification_budget_clusters: int | None = None,
) -> dict[str, Any]:
    """Emit a payload compatible with the shared CandidateSet contract."""

    return {
        "search_id": search_id,
        "query_plan_version": query_plan_version,
        "channels_run": list(channels_run),
        "candidate_recall_operating_point": candidate_recall_operating_point,
        "exact_scoring_completed": exact_scoring_completed,
        "windows": [candidate_window_payload(window) for window in windows],
        "clusters": [candidate_cluster_payload(cluster) for cluster in clusters],
        "graph_execution": dict(graph_execution or {}),
        "assembly": dict(assembly or {}),
        "verification_budget_clusters": verification_budget_clusters,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Union semantic/graph candidates, RRF-order, and cluster"
    )
    parser.add_argument("input_json")
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--adjacent-gap", type=float, default=2.0)
    args = parser.parse_args(argv)
    with open(args.input_json, encoding="utf-8") as source:
        payload = json.load(source)
    union = union_semantic_and_graph(
        payload.get("semantic_candidates", ()),
        payload.get("graph_candidates", ()),
        thresholds=payload.get("thresholds"),
    )
    ordered = reciprocal_rank_order(union, k=args.rrf_k)
    clusters = cluster_candidates(ordered, adjacent_gap_s=args.adjacent_gap)
    print(
        json.dumps(
            {
                "windows": [
                    candidate_window_payload(candidate) for candidate in ordered
                ],
                "clusters": [
                    candidate_cluster_payload(cluster) for cluster in clusters
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
