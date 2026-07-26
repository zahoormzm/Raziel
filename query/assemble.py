"""Cross-window, same-camera temporal episode assembly."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import product
import json
from typing import Any, Mapping, Sequence

from query.schema import (
    CandidateWindowData,
    EvidenceRefData,
    QueryPlanData,
    TemporalRelation,
    candidate_window_payload,
    coerce_candidate_window,
    coerce_query_plan,
    stable_identifier,
    unique_preserving_order,
)

# Episodes are produced by joining per-atom anchors. The join is allowed a
# multiple of the requested episode cap so a legitimate result set is never
# starved, while a pathological product still terminates. Hitting it marks the
# assembly episode-cap-bound, which makes completeness False -- a truncated
# search must never read as a complete one.
_JOIN_BUDGET_FACTOR = 64

# Applied when the caller supplies no episode cap. assemble_temporal's own
# signature defaults max_episode_count to None, and the module CLI's
# --episode-cap defaults to None, so before this the default path -- the one
# most in need of a safety limit -- was the only path with no limit at all.
_DEFAULT_JOIN_BUDGET = 200_000

# Rejections are diagnostic output that assembly_result_payload serialises in
# full. Cap what is retained.
_MAX_REPORTED_REJECTIONS = 500


@dataclass(frozen=True)
class LabeledSubsegment:
    atom_id: str
    candidate_id: str
    t0: float
    t1: float


@dataclass(frozen=True)
class AssembledEpisode:
    episode: CandidateWindowData
    labeled_subsegments: tuple[LabeledSubsegment, ...]
    graph_trace_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssemblyRejection:
    first_candidate_id: str
    second_candidate_id: str
    reason: str


@dataclass(frozen=True)
class AssemblyCompletenessData:
    anchor_candidates_qualifying: int
    anchor_candidates_retained: int
    episodes_generated: int
    episode_cap_bound: bool
    assembly_complete: bool


@dataclass(frozen=True)
class AssemblyResult:
    episodes: tuple[AssembledEpisode, ...]
    completeness: AssemblyCompletenessData
    rejected_joins: tuple[AssemblyRejection, ...]


def _same_camera(left: CandidateWindowData, right: CandidateWindowData) -> bool:
    return (
        left.video_id == right.video_id
        and left.camera_id == right.camera_id
    )


def _ordered_pair(
    first: CandidateWindowData,
    second: CandidateWindowData,
    relation: TemporalRelation,
) -> tuple[bool, str | None]:
    if not _same_camera(first, second):
        return False, "cross_camera_or_video_join"
    if relation.relation == "after":
        first, second = second, first
    gap = second.t0 - first.t1
    if gap < 0:
        return False, "wrong_order_or_overlapping_anchors"
    if gap > relation.max_gap_s:
        return False, "maximum_gap_exceeded"
    if relation.same_actor_required:
        if relation.max_gap_s > 30:
            return False, "unsafe_long_gap_identity"
        shared_tracks = set(first.tracklet_ids).intersection(second.tracklet_ids)
        if not shared_tracks:
            return False, "same_actor_not_visibly_continuous"
    return True, None


def _relation_candidates(
    relation: TemporalRelation,
    anchor_candidates: Mapping[str, tuple[CandidateWindowData, ...]],
    *,
    scan_budget: int,
    rejection_cap: int = _MAX_REPORTED_REJECTIONS,
) -> tuple[
    list[tuple[CandidateWindowData, CandidateWindowData]],
    list[AssemblyRejection],
    bool,
]:
    """Pair anchors for one relation under an explicit scan budget.

    This scan is the dominant cost, not the episode product below it: it is
    O(first x second) and previously ran to completion before any budget was
    consulted, building one AssemblyRejection per invalid pair. With 700 anchors
    per atom that materialised 490,000 rejection objects -- all of which
    assembly_result_payload then serialises -- before the episode budget was
    reached at 512 combinations. Bounding the product alone moved the
    generate-then-truncate one loop earlier rather than removing it.

    Rejections are also capped: they are diagnostic output, and retaining a
    half-million of them helps nobody.
    """
    pairs: list[tuple[CandidateWindowData, CandidateWindowData]] = []
    rejected: list[AssemblyRejection] = []
    scanned = 0
    truncated = False
    for first in anchor_candidates.get(relation.first_atom, ()):
        if truncated:
            break
        for second in anchor_candidates.get(relation.second_atom, ()):
            scanned += 1
            if scanned > scan_budget:
                truncated = True
                break
            valid, reason = _ordered_pair(first, second, relation)
            if valid:
                pairs.append((first, second))
            elif len(rejected) < rejection_cap:
                rejected.append(
                    AssemblyRejection(
                        first_candidate_id=first.candidate_id,
                        second_candidate_id=second.candidate_id,
                        reason=reason or "join_rejected",
                    )
                )
    return pairs, rejected, truncated


def _consistent_selection(
    selected: Mapping[str, CandidateWindowData],
    temporal_relations: Sequence[TemporalRelation],
) -> bool:
    for relation in temporal_relations:
        first = selected.get(relation.first_atom)
        second = selected.get(relation.second_atom)
        if first is None or second is None:
            return False
        valid, _reason = _ordered_pair(first, second, relation)
        if not valid:
            return False
    values = tuple(selected.values())
    return all(_same_camera(values[0], item) for item in values[1:])


def _make_episode(
    plan: QueryPlanData,
    selected: Mapping[str, CandidateWindowData],
) -> AssembledEpisode:
    ordered = tuple(
        sorted(selected.items(), key=lambda item: (item[1].t0, item[0]))
    )
    candidates = tuple(candidate for _atom_id, candidate in ordered)
    first = candidates[0]
    evidence_by_id: dict[str, EvidenceRefData] = {}
    for candidate in candidates:
        for evidence in candidate.evidence:
            evidence_by_id.setdefault(evidence.node_id, evidence)
    trace_ids = unique_preserving_order(
        trace_id for candidate in candidates for trace_id in candidate.trace_ids
    )
    member_ids = tuple(candidate.candidate_id for candidate in candidates)
    # The atom binding is part of the episode's identity. Without it, two
    # episodes that bind the same candidate windows to different atoms collided
    # in episodes_by_id and one was silently discarded -- while the completeness
    # record still reported assembly_complete=True. api/pipeline.py builds anchors
    # as {atom_id: [items where atom_id in item.atom_ids]}, so any candidate
    # window carrying two atom_ids lands under both keys and hits this. A recall
    # loss reported as a complete assembly is exactly what the truthfulness
    # contract forbids.
    episode = CandidateWindowData(
        candidate_id=stable_identifier(
            "episode",
            (
                plan.schema_version,
                member_ids,
                tuple(atom_id for atom_id, _candidate in ordered),
            ),
        ),
        video_id=first.video_id,
        camera_id=first.camera_id,
        t0=min(candidate.t0 for candidate in candidates),
        t1=max(candidate.t1 for candidate in candidates),
        channel_scores={
            "assembly:temporal": max(
                (
                    candidate.rrf_score
                    if candidate.rrf_score is not None
                    else max(candidate.channel_scores.values())
                )
                for candidate in candidates
            )
        },
        qualifying_channels=("assembly:temporal",),
        evidence=tuple(evidence_by_id.values()),
        atom_ids=tuple(atom_id for atom_id, _candidate in ordered),
        tracklet_ids=unique_preserving_order(
            tracklet
            for candidate in candidates
            for tracklet in candidate.tracklet_ids
        ),
        trace_ids=unique_preserving_order(
            (
                *trace_ids,
                *(
                    f"{relation.first_atom}:{relation.relation}:"
                    f"{relation.second_atom}:max_gap={relation.max_gap_s}"
                    for relation in plan.temporal_relations
                ),
            )
        ),
    )
    return AssembledEpisode(
        episode=episode,
        labeled_subsegments=tuple(
            LabeledSubsegment(
                atom_id=atom_id,
                candidate_id=candidate.candidate_id,
                t0=candidate.t0,
                t1=candidate.t1,
            )
            for atom_id, candidate in ordered
        ),
        graph_trace_ids=trace_ids,
    )


def assemble_temporal(
    plan_value: QueryPlanData | Any,
    anchors: Mapping[str, Sequence[CandidateWindowData | Any]],
    *,
    max_episode_count: int | None = None,
) -> AssemblyResult:
    """Join every qualifying anchor first; apply an explicit cap only after."""

    plan = coerce_query_plan(plan_value)
    if max_episode_count is not None and max_episode_count < 0:
        raise ValueError("max_episode_count cannot be negative")
    normalised = {
        atom_id: tuple(coerce_candidate_window(value) for value in values)
        for atom_id, values in anchors.items()
    }
    qualifying = sum(len(values) for values in normalised.values())
    retained = qualifying  # no anchor pre-truncation is allowed
    rejected: list[AssemblyRejection] = []
    join_budget_reached = False
    join_budget = (
        max_episode_count * _JOIN_BUDGET_FACTOR
        if max_episode_count is not None
        else _DEFAULT_JOIN_BUDGET
    )
    for relation in plan.temporal_relations:
        _pairs, relation_rejections, scan_truncated = _relation_candidates(
            relation, normalised, scan_budget=join_budget
        )
        rejected.extend(relation_rejections)
        join_budget_reached = join_budget_reached or scan_truncated

    if not plan.temporal_relations:
        episodes = [
            _make_episode(plan, {atom_id: candidate})
            for atom_id, candidates in normalised.items()
            for candidate in candidates
        ]
    else:
        required_atoms = tuple(
            dict.fromkeys(
                atom_id
                for relation in plan.temporal_relations
                for atom_id in (relation.first_atom, relation.second_atom)
            )
        )
        if any(not normalised.get(atom_id) for atom_id in required_atoms):
            episodes = []
        else:
            # Bound the join rather than generate-then-truncate. The full product
            # was materialised before max_episode_count applied, so a three-atom
            # temporal query with 100 anchors per atom evaluated
            # _consistent_selection a million times and built the whole list
            # before the cap trimmed it -- the cap bounded the output, not the
            # work. The sibling executor in query/graph_execute.py enforces its
            # join_budget inside the loop for the same reason.
            episodes = []
            joins = 0
            for combination in product(
                *(normalised[atom_id] for atom_id in required_atoms)
            ):
                joins += 1
                if joins > join_budget:
                    join_budget_reached = True
                    break
                selection = dict(zip(required_atoms, combination))
                if _consistent_selection(selection, plan.temporal_relations):
                    episodes.append(_make_episode(plan, selection))

    # Equivalent episodes can arise when two relations share anchors.
    episodes_by_id = {
        episode.episode.candidate_id: episode for episode in episodes
    }
    all_episodes = tuple(
        sorted(
            episodes_by_id.values(),
            key=lambda item: (
                item.episode.video_id,
                item.episode.camera_id or "",
                item.episode.t0,
                item.episode.t1,
            ),
        )
    )
    cap_bound = join_budget_reached or (
        max_episode_count is not None and len(all_episodes) > max_episode_count
    )
    returned = (
        all_episodes[:max_episode_count]
        if max_episode_count is not None
        else all_episodes
    )
    complete = retained == qualifying and not cap_bound
    return AssemblyResult(
        episodes=returned,
        completeness=AssemblyCompletenessData(
            anchor_candidates_qualifying=qualifying,
            anchor_candidates_retained=retained,
            episodes_generated=len(all_episodes),
            episode_cap_bound=cap_bound,
            assembly_complete=complete,
        ),
        rejected_joins=tuple(rejected),
    )


def assembly_completeness_payload(value: AssemblyCompletenessData) -> dict[str, Any]:
    return {
        "anchor_candidates_qualifying": value.anchor_candidates_qualifying,
        "anchor_candidates_retained": value.anchor_candidates_retained,
        "episodes_generated": value.episodes_generated,
        "episode_cap_bound": value.episode_cap_bound,
        "assembly_complete": value.assembly_complete,
    }


def assembly_result_payload(result: AssemblyResult) -> dict[str, Any]:
    return {
        "episodes": [
            {
                **candidate_window_payload(item.episode),
                "labeled_subsegments": [
                    segment.__dict__ for segment in item.labeled_subsegments
                ],
                "graph_trace_ids": list(item.graph_trace_ids),
            }
            for item in result.episodes
        ],
        "completeness": assembly_completeness_payload(result.completeness),
        "rejected_joins": [item.__dict__ for item in result.rejected_joins],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble temporal anchor candidates")
    parser.add_argument("input_json")
    parser.add_argument("--episode-cap", type=int)
    args = parser.parse_args(argv)
    with open(args.input_json, encoding="utf-8") as source:
        payload = json.load(source)
    result = assemble_temporal(
        payload["query_plan"],
        payload["anchors"],
        max_episode_count=args.episode_cap,
    )
    print(json.dumps(assembly_result_payload(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
