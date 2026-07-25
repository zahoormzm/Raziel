"""Fixed evidence predicate registry and observation-safety rules.

No parser/model-provided executable is accepted here.  Query predicates are
resolved through this registry, and absence/count outcomes are conservative
with respect to coverage, assessability, and track fragmentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from query.schema import PredicateState


@dataclass(frozen=True)
class PredicateDecision:
    state: PredicateState
    reason_code: str
    evidence_ids: tuple[str, ...] = ()
    observed_count: int | None = None


@dataclass(frozen=True)
class ObservationSafety:
    expected_ticks: int
    observed_ticks: int
    assessable_ticks: int
    occluded_ticks: int = 0
    low_light_ticks: int = 0
    region_assessable: bool = True
    coverage_complete: bool | None = None

    @property
    def complete(self) -> bool:
        if self.coverage_complete is not None:
            return self.coverage_complete
        return self.expected_ticks > 0 and self.observed_ticks >= self.expected_ticks

    @property
    def assessable(self) -> bool:
        return (
            self.region_assessable
            and self.assessable_ticks > 0
            and self.occluded_ticks < self.observed_ticks
            and self.low_light_ticks < self.observed_ticks
        )


@dataclass(frozen=True)
class TrackletObservation:
    tracklet_id: str
    label: str
    video_id: str
    camera_id: str | None
    t0: float
    t1: float
    continuity: str = "continuous"
    fragment_group: str | None = None


def _norm(value: str) -> str:
    return value.strip().casefold().replace(" ", "_")


def temporal_overlaps(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return float(left["t0"]) <= float(right["t1"]) and float(right["t0"]) <= float(left["t1"])


def temporal_precedes(
    left: Mapping[str, object], right: Mapping[str, object], *, max_gap_s: float = 600.0
) -> bool:
    gap = float(right["t0"]) - float(left["t1"])
    return 0 <= gap <= max_gap_s


def temporal_follows(
    left: Mapping[str, object], right: Mapping[str, object], *, max_gap_s: float = 600.0
) -> bool:
    return temporal_precedes(right, left, max_gap_s=max_gap_s)


def payload_contains_label(payload: Mapping[str, object], expected: str) -> bool:
    expected_norm = _norm(expected)
    values: list[str] = []
    for key in ("label", "class", "text", "caption", "zone_id", "action", "attribute"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value)
    searchable = " ".join(_norm(item) for item in values)
    return expected_norm in searchable or all(
        token in searchable for token in expected_norm.split("_")
    )


# Storage-level edges are exactly those declared in the master plan's graph
# schema.  Higher-level "wears/places/picks_up" relations remain semantic or
# verifier constraints unless their producer emits a registered evidence edge.
EDGE_PREDICATES = frozenset(
    {
        "overlaps",
        "precedes",
        "follows",
        "near",
        "contains",
        "belongs_to_track",
        "carries",
        "enters",
        "exits",
        "co_occurs",
    }
)
QUERY_TO_EDGE_PREDICATE: Mapping[str, str | None] = {
    "carries": "carries",
    "near": "near",
    "follows": "follows",
    "wears": None,
    "places": None,
    "picks_up": None,
}


def resolve_edge_predicate(query_predicate: str) -> str | None:
    if query_predicate not in QUERY_TO_EDGE_PREDICATE:
        raise ValueError(f"Predicate is outside the fixed registry: {query_predicate!r}")
    return QUERY_TO_EDGE_PREDICATE[query_predicate]


def evaluate_visible_none(
    *,
    target_evidence_ids: Iterable[str],
    safety: ObservationSafety,
) -> PredicateDecision:
    """Evaluate visible absence only within a complete, assessable scope."""

    evidence = tuple(dict.fromkeys(str(item) for item in target_evidence_ids))
    if not safety.complete:
        return PredicateDecision(
            PredicateState.UNDETERMINED,
            "incomplete_observation_coverage",
            evidence,
        )
    if not safety.assessable:
        reason = (
            "low_light"
            if safety.low_light_ticks >= max(1, safety.observed_ticks)
            else "occlusion_or_unassessable_region"
        )
        return PredicateDecision(PredicateState.UNOBSERVABLE, reason, evidence)
    if evidence:
        return PredicateDecision(
            PredicateState.CONTRADICTED,
            "target_visibly_present",
            evidence,
        )
    return PredicateDecision(PredicateState.SUPPORTED, "complete_visible_absence")


def evaluate_bounded_count(
    *,
    tracklets: Iterable[TrackletObservation],
    target_label: str,
    min_count: int,
    max_count: int,
    safety: ObservationSafety,
    fragmentation_tolerance: int = 0,
) -> PredicateDecision:
    """Count safe anonymous tracklets within one continuous camera interval."""

    if not safety.complete:
        return PredicateDecision(
            PredicateState.UNDETERMINED, "incomplete_observation_coverage"
        )
    if not safety.assessable:
        reason = (
            "low_light"
            if safety.low_light_ticks >= max(1, safety.observed_ticks)
            else "occlusion_or_unassessable_region"
        )
        return PredicateDecision(PredicateState.UNOBSERVABLE, reason)

    selected = [
        tracklet for tracklet in tracklets if _norm(tracklet.label) == _norm(target_label)
    ]
    cameras = {(tracklet.video_id, tracklet.camera_id) for tracklet in selected}
    if len(cameras) > 1:
        return PredicateDecision(PredicateState.UNDETERMINED, "cross_camera_count")
    interrupted = sum(tracklet.continuity != "continuous" for tracklet in selected)
    fragment_groups: dict[str, int] = {}
    for tracklet in selected:
        if tracklet.fragment_group:
            fragment_groups[tracklet.fragment_group] = (
                fragment_groups.get(tracklet.fragment_group, 0) + 1
            )
    duplicate_fragments = sum(max(0, amount - 1) for amount in fragment_groups.values())
    if interrupted + duplicate_fragments > fragmentation_tolerance:
        return PredicateDecision(
            PredicateState.UNDETERMINED,
            "excessive_track_fragmentation",
            tuple(tracklet.tracklet_id for tracklet in selected),
        )

    # A producer-approved fragment_group denotes pieces of one anonymous local
    # trajectory.  It may be merged only inside the same camera/session.
    identities = {
        tracklet.fragment_group or tracklet.tracklet_id for tracklet in selected
    }
    count = len(identities)
    state = (
        PredicateState.SUPPORTED
        if min_count <= count <= max_count
        else PredicateState.CONTRADICTED
    )
    return PredicateDecision(
        state,
        "count_within_bounds" if state is PredicateState.SUPPORTED else "count_out_of_bounds",
        tuple(tracklet.tracklet_id for tracklet in selected),
        observed_count=count,
    )


PredicateFunction = Callable[..., bool]
PREDICATE_REGISTRY: Mapping[str, PredicateFunction] = {
    "overlaps": temporal_overlaps,
    "precedes": temporal_precedes,
    "follows": temporal_follows,
}


def get_predicate(name: str) -> PredicateFunction:
    try:
        return PREDICATE_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"No executable registered predicate: {name!r}") from exc
