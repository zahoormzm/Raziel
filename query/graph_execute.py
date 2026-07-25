"""Bounded executor for fixed, parameterized temporal-evidence patterns."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from evidence.predicates import (
    EDGE_PREDICATES,
    ObservationSafety,
    PredicateDecision,
    TrackletObservation,
    evaluate_bounded_count,
    evaluate_visible_none,
    payload_contains_label,
    temporal_precedes,
)
from query.schema import (
    CandidateWindowData,
    EdgeConstraint,
    EvidenceRefData,
    GraphPattern,
    LogicConstraint,
    LogicOperator,
    NodeConstraint,
    PredicateState,
    TemporalRelation,
    boundary_mapping,
    candidate_window_payload,
    stable_identifier,
)


@dataclass(frozen=True)
class EvidenceNode:
    node_id: str
    node_type: str
    video_id: str
    camera_id: str | None
    t0: float
    t1: float
    payload: Mapping[str, Any]
    producer_version: str


@dataclass(frozen=True)
class EvidenceEdge:
    edge_id: str
    subject_node_id: str
    predicate: str
    object_node_id: str
    t0: float | None
    t1: float | None
    evidence: Mapping[str, Any]
    producer_version: str


@dataclass(frozen=True)
class GraphExecutionTraceData:
    enabled: bool
    pattern_id: str | None
    tracklets_considered: int
    joins_completed: int
    join_budget_reached: bool
    observation_scope_assessable: bool | None
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class GraphExecutionResult:
    candidates: tuple[CandidateWindowData, ...]
    trace: GraphExecutionTraceData
    logic_decisions: Mapping[str, PredicateDecision]


NODE_SELECT_ACTIVE_SCOPED = """
SELECT node_id, node_type, video_id, t0, t1, payload_json, producer_version
FROM active_evidence_nodes
WHERE video_id = ? AND t1 >= ? AND t0 <= ?
ORDER BY t0, node_id
LIMIT ?
"""
NODE_SELECT_ACTIVE_TIME = """
SELECT node_id, node_type, video_id, t0, t1, payload_json, producer_version
FROM active_evidence_nodes
WHERE t1 >= ? AND t0 <= ?
ORDER BY video_id, t0, node_id
LIMIT ?
"""
NODE_SELECT_LEGACY_SCOPED = """
SELECT node_id, node_type, video_id, t0, t1, payload_json, producer_version
FROM evidence_nodes
WHERE video_id = ? AND t1 >= ? AND t0 <= ?
ORDER BY t0, node_id
LIMIT ?
"""
NODE_SELECT_LEGACY_TIME = """
SELECT node_id, node_type, video_id, t0, t1, payload_json, producer_version
FROM evidence_nodes
WHERE t1 >= ? AND t0 <= ?
ORDER BY video_id, t0, node_id
LIMIT ?
"""
EDGE_SELECT_ACTIVE_PREDICATE = """
SELECT edge_id, subject_node_id, predicate, object_node_id, t0, t1,
       evidence_json, producer_version
FROM active_evidence_edges
WHERE predicate = ?
ORDER BY edge_id
LIMIT ?
"""
EDGE_SELECT_LEGACY_PREDICATE = """
SELECT edge_id, subject_node_id, predicate, object_node_id, t0, t1,
       evidence_json, producer_version
FROM evidence_edges
WHERE predicate = ?
ORDER BY edge_id
LIMIT ?
"""


def _active_views_available(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = ? AND name IN (?, ?)
        """,
        ("view", "active_evidence_nodes", "active_evidence_edges"),
    ).fetchall()
    present = {str(row[0]) for row in rows}
    required = {
        "active_evidence_nodes",
        "active_evidence_edges",
    }
    if present and present != required:
        raise RuntimeError(
            "incomplete active evidence view schema; refusing legacy fallback"
        )
    return present == required


def _loads_mapping(value: str | None) -> Mapping[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, Mapping) else {"value": parsed}


def _node_from_row(
    row: Sequence[Any], camera_by_video: Mapping[str, str | None] | None = None
) -> EvidenceNode:
    payload = _loads_mapping(row[5])
    camera = payload.get("camera_id")
    if camera is None and camera_by_video is not None:
        camera = camera_by_video.get(str(row[2]))
    return EvidenceNode(
        node_id=str(row[0]),
        node_type=str(row[1]),
        video_id=str(row[2]),
        camera_id=None if camera is None else str(camera),
        t0=float(row[3]),
        t1=float(row[4]),
        payload=payload,
        producer_version=str(row[6]),
    )


def _edge_from_row(row: Sequence[Any]) -> EvidenceEdge:
    return EvidenceEdge(
        edge_id=str(row[0]),
        subject_node_id=str(row[1]),
        predicate=str(row[2]),
        object_node_id=str(row[3]),
        t0=None if row[4] is None else float(row[4]),
        t1=None if row[5] is None else float(row[5]),
        evidence=_loads_mapping(row[6]),
        producer_version=str(row[7]),
    )


def load_bounded_graph(
    connection: sqlite3.Connection,
    pattern: GraphPattern,
    *,
    node_scan_budget: int | None = None,
    edge_scan_budget: int | None = None,
) -> tuple[tuple[EvidenceNode, ...], tuple[EvidenceEdge, ...], bool]:
    """Load via fixed statements; pattern text is never interpolated into SQL."""

    scan_limit = node_scan_budget or max(1_000, pattern.join_budget * 4)
    edge_limit = edge_scan_budget or max(1_000, pattern.join_budget * 4)
    active_views = _active_views_available(connection)
    scoped_statement = (
        NODE_SELECT_ACTIVE_SCOPED
        if active_views
        else NODE_SELECT_LEGACY_SCOPED
    )
    time_statement = (
        NODE_SELECT_ACTIVE_TIME if active_views else NODE_SELECT_LEGACY_TIME
    )
    edge_statement = (
        EDGE_SELECT_ACTIVE_PREDICATE
        if active_views
        else EDGE_SELECT_LEGACY_PREDICATE
    )
    video_ids = tuple(
        dict.fromkeys(video for node in pattern.nodes for video in node.video_ids)
    )
    starts = [node.start_time for node in pattern.nodes if node.start_time is not None]
    ends = [node.end_time for node in pattern.nodes if node.end_time is not None]
    start = min(starts) if starts else 0.0
    end = max(ends) if ends else 10**15
    rows: list[Sequence[Any]] = []
    if video_ids:
        per_video_limit = max(1, scan_limit // len(video_ids))
        for video_id in video_ids:
            rows.extend(
                connection.execute(
                    scoped_statement,
                    (video_id, start, end, per_video_limit + 1),
                ).fetchall()
            )
    else:
        rows.extend(
            connection.execute(
                time_statement, (start, end, scan_limit + 1)
            ).fetchall()
        )
    node_scan_bound = len(rows) > scan_limit
    rows = rows[:scan_limit]
    try:
        camera_by_video = {
            str(video_id): None if camera_id is None else str(camera_id)
            for video_id, camera_id in connection.execute(
                "SELECT video_id, camera_id FROM videos"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        camera_by_video = {}
    nodes = tuple(_node_from_row(row, camera_by_video) for row in rows)
    node_ids = {node.node_id for node in nodes}
    required_predicates = tuple(
        dict.fromkeys(
            (
                *(edge.predicate for edge in pattern.edges),
                "belongs_to_track",
            )
        )
    )
    per_predicate_limit = max(1, edge_limit // len(required_predicates))
    edge_rows: list[Sequence[Any]] = []
    for predicate in required_predicates:
        edge_rows.extend(
            connection.execute(
                edge_statement,
                (predicate, per_predicate_limit + 1),
            ).fetchall()
        )
    edge_scan_bound = len(edge_rows) > edge_limit
    edges = tuple(
        edge
        for edge in (_edge_from_row(row) for row in edge_rows[:edge_limit])
        if edge.subject_node_id in node_ids and edge.object_node_id in node_ids
    )
    return nodes, edges, node_scan_bound or edge_scan_bound


def _same_scope(left: EvidenceNode, right: EvidenceNode) -> bool:
    if left.video_id != right.video_id:
        return False
    return left.camera_id == right.camera_id


def _in_scope(constraint: NodeConstraint, node: EvidenceNode) -> bool:
    if node.node_type not in constraint.allowed_node_types:
        return False
    if constraint.video_ids and node.video_id not in constraint.video_ids:
        return False
    if constraint.camera_ids and node.camera_id not in constraint.camera_ids:
        return False
    if constraint.start_time is not None and node.t1 < constraint.start_time:
        return False
    if constraint.end_time is not None and node.t0 > constraint.end_time:
        return False
    return True


def _matches(constraint: NodeConstraint, node: EvidenceNode) -> bool:
    return _in_scope(constraint, node) and payload_contains_label(
        node.payload, constraint.text
    )


def _edge_lookup(edges: Sequence[EvidenceEdge]) -> dict[tuple[str, str, str], EvidenceEdge]:
    result: dict[tuple[str, str, str], EvidenceEdge] = {}
    for edge in edges:
        if edge.predicate not in EDGE_PREDICATES:
            continue
        result[(edge.subject_node_id, edge.predicate, edge.object_node_id)] = edge
    return result


def _binding_valid(
    binding: Mapping[str, EvidenceNode],
    pattern: GraphPattern,
    edge_map: Mapping[tuple[str, str, str], EvidenceEdge],
) -> tuple[bool, tuple[str, ...]]:
    nodes = tuple(binding.values())
    if any(not _same_scope(nodes[0], node) for node in nodes[1:]):
        return False, ()
    used_edges: list[str] = []
    for constraint in pattern.edges:
        if (
            constraint.subject_variable not in binding
            or constraint.object_variable not in binding
        ):
            continue
        edge = edge_map.get(
            (
                binding[constraint.subject_variable].node_id,
                constraint.predicate,
                binding[constraint.object_variable].node_id,
            )
        )
        if edge is None and constraint.required:
            return False, ()
        if edge is not None:
            used_edges.append(edge.edge_id)
    for relation in pattern.temporal_relations:
        first_name = f"v_{relation.first_atom}"
        second_name = f"v_{relation.second_atom}"
        if first_name not in binding or second_name not in binding:
            continue
        first, second = binding[first_name], binding[second_name]
        if relation.relation == "after":
            first, second = second, first
        if not temporal_precedes(
            {"t0": first.t0, "t1": first.t1},
            {"t0": second.t0, "t1": second.t1},
            max_gap_s=relation.max_gap_s,
        ):
            return False, ()
        if relation.same_actor_required:
            first_track = (
                first.node_id
                if first.node_type == "tracklet"
                else first.payload.get("tracklet_id")
            )
            second_track = (
                second.node_id
                if second.node_type == "tracklet"
                else second.payload.get("tracklet_id")
            )
            if first_track is None or first_track != second_track:
                return False, ()
    return True, tuple(used_edges)


def _predicate_scope_matches(value: Any, predicate_scope: str) -> bool:
    if isinstance(value, str):
        return value == predicate_scope
    if isinstance(value, Mapping):
        return value.get("group_id") == predicate_scope
    if isinstance(value, (list, tuple, set)):
        return predicate_scope in value
    return False


def _observation_nodes(
    nodes: Sequence[EvidenceNode], *, predicate_scope: str | None = None
) -> list[EvidenceNode]:
    observation_nodes = [
        node
        for node in nodes
        if node.node_type in {"frame", "window", "episode"}
        and any(
            key in node.payload
            for key in (
                "expected_ticks",
                "observed_ticks",
                "assessable_ticks",
                "coverage_complete",
                "assessable",
                "occluded",
                "low_light",
            )
        )
    ]
    if predicate_scope is not None:
        explicit_episode_nodes = [
            node
            for node in observation_nodes
            if node.node_type == "episode"
            and _predicate_scope_matches(
                node.payload.get("predicate_scope"), predicate_scope
            )
        ]
        if explicit_episode_nodes:
            observation_nodes = explicit_episode_nodes
    return observation_nodes


def _observation_safety(
    nodes: Sequence[EvidenceNode], *, predicate_scope: str | None = None
) -> ObservationSafety:
    observation_nodes = _observation_nodes(
        nodes, predicate_scope=predicate_scope
    )
    if not observation_nodes:
        return ObservationSafety(1, 0, 0, coverage_complete=False)
    expected = sum(int(node.payload.get("expected_ticks", 1)) for node in observation_nodes)
    observed = sum(
        int(node.payload.get("observed_ticks", 1)) for node in observation_nodes
    )
    assessable = sum(
        int(
            node.payload.get(
                "assessable_ticks",
                0 if node.payload.get("assessable") is False else 1,
            )
        )
        for node in observation_nodes
    )
    occluded = sum(
        int(node.payload.get("occluded_ticks", bool(node.payload.get("occluded", False))))
        for node in observation_nodes
    )
    low_light = sum(
        int(node.payload.get("low_light_ticks", bool(node.payload.get("low_light", False))))
        for node in observation_nodes
    )
    explicitly_complete = all(
        node.payload.get("coverage_complete", True) for node in observation_nodes
    )
    return ObservationSafety(
        expected_ticks=expected,
        observed_ticks=observed,
        assessable_ticks=assessable,
        occluded_ticks=occluded,
        low_light_ticks=low_light,
        region_assessable=all(
            node.payload.get(
                "region_assessable",
                node.payload.get("assessable", True),
            )
            and node.payload.get("occlusion_assessed", True)
            for node in observation_nodes
        ),
        coverage_complete=explicitly_complete and observed >= expected,
    )


def _logic_variables(pattern: GraphPattern, operator: LogicOperator) -> set[str]:
    return {
        variable
        for group in pattern.logic_constraints
        if group.operator is operator
        for variable in group.variables
    }


def _candidate_from_binding(
    pattern: GraphPattern,
    binding: Mapping[str, EvidenceNode],
    used_edges: Sequence[str],
) -> CandidateWindowData:
    nodes = tuple(binding.values())
    first = nodes[0]
    evidence = tuple(
        EvidenceRefData(
            node_id=node.node_id,
            node_type=node.node_type,
            video_id=node.video_id,
            camera_id=node.camera_id,
            t0=node.t0,
            t1=node.t1,
            frame_ids=tuple(int(v) for v in node.payload.get("frame_ids", ())),
            producer_version=node.producer_version,
        )
        for node in nodes
    )
    return CandidateWindowData(
        candidate_id=stable_identifier(
            "gc",
            (pattern.pattern_id, tuple(sorted(node.node_id for node in nodes))),
        ),
        video_id=first.video_id,
        camera_id=first.camera_id,
        t0=min(node.t0 for node in nodes),
        t1=max(node.t1 for node in nodes) + 1e-6,
        channel_scores={"graph:pattern": 1.0},
        qualifying_channels=("graph:pattern",),
        evidence=evidence,
        atom_ids=tuple(
            variable.removeprefix("v_") for variable in binding
        ),
        tracklet_ids=tuple(
            node.node_id for node in nodes if node.node_type == "tracklet"
        ),
        trace_ids=tuple((pattern.pattern_id, *used_edges)),
    )


def _logic_scope_candidate(
    pattern: GraphPattern, nodes: Sequence[EvidenceNode], group_id: str
) -> CandidateWindowData | None:
    observation_nodes = [
        node for node in nodes if node.node_type in {"frame", "window", "episode"}
    ]
    if not observation_nodes:
        return None
    first = observation_nodes[0]
    return CandidateWindowData(
        candidate_id=stable_identifier(
            "gc", (pattern.pattern_id, group_id, first.video_id, first.camera_id)
        ),
        video_id=first.video_id,
        camera_id=first.camera_id,
        t0=min(node.t0 for node in observation_nodes),
        t1=max(node.t1 for node in observation_nodes) + 1e-6,
        channel_scores={"graph:pattern": 1.0},
        qualifying_channels=("graph:pattern",),
        evidence=tuple(
            EvidenceRefData(
                node_id=node.node_id,
                node_type=node.node_type,
                video_id=node.video_id,
                camera_id=node.camera_id,
                t0=node.t0,
                t1=node.t1,
                frame_ids=tuple(int(v) for v in node.payload.get("frame_ids", ())),
                producer_version=node.producer_version,
            )
            for node in observation_nodes
        ),
        trace_ids=(pattern.pattern_id, group_id),
    )


def execute_pattern(
    pattern: GraphPattern,
    nodes: Sequence[EvidenceNode],
    edges: Sequence[EvidenceEdge],
    *,
    scan_budget_reached: bool = False,
    fragmentation_tolerance: int = 0,
) -> GraphExecutionResult:
    """Execute bounded joins and conservative logic over typed evidence."""

    if pattern.join_budget <= 0:
        raise ValueError("join budget must be positive")
    invalid_predicates = [
        edge.predicate for edge in pattern.edges if edge.predicate not in EDGE_PREDICATES
    ]
    if invalid_predicates:
        raise ValueError(
            f"pattern contains predicates outside the fixed registry: {invalid_predicates}"
        )
    if any(
        relation.same_actor_required and relation.max_gap_s > 30
        for relation in pattern.temporal_relations
    ):
        raise ValueError("unsafe long-gap same-actor graph pattern")
    edge_map = _edge_lookup(edges)
    nodes_by_id = {node.node_id: node for node in nodes}
    tracklet_linked_payloads: dict[str, list[Mapping[str, Any]]] = {}
    for edge in edges:
        if edge.predicate != "belongs_to_track":
            continue
        source = nodes_by_id.get(edge.subject_node_id)
        target = nodes_by_id.get(edge.object_node_id)
        if source is not None and target is not None and target.node_type == "tracklet":
            tracklet_linked_payloads.setdefault(target.node_id, []).append(source.payload)
    match_sets = {
        constraint.variable: tuple(
            node
            for node in nodes
            if _matches(constraint, node)
            or (
                node.node_type == "tracklet"
                and _in_scope(constraint, node)
                and any(
                    payload_contains_label(payload, constraint.text)
                    for payload in tracklet_linked_payloads.get(node.node_id, ())
                )
            )
        )
        for constraint in pattern.nodes
    }
    negative_vars = _logic_variables(pattern, LogicOperator.VISIBLE_NONE)
    count_vars = _logic_variables(pattern, LogicOperator.COUNT)
    any_vars = _logic_variables(pattern, LogicOperator.ANY)
    required_variables = [
        constraint.variable
        for constraint in pattern.nodes
        if constraint.variable not in negative_vars
        and constraint.variable not in count_vars
        and constraint.variable not in any_vars
    ]
    # An ANY group contributes one selected alternative, not a Cartesian
    # requirement that every alternative be present.
    any_groups = [
        group
        for group in pattern.logic_constraints
        if group.operator is LogicOperator.ANY
    ]
    join_sets: list[tuple[str, tuple[EvidenceNode, ...]]] = [
        (variable, match_sets[variable]) for variable in required_variables
    ]
    for group in any_groups:
        alternatives = tuple(
            node
            for variable in group.variables
            for node in match_sets.get(variable, ())
        )
        join_sets.append((f"__any__{group.group_id}", alternatives))

    bindings: list[dict[str, EvidenceNode]] = [dict()]
    joins = 0
    budget_reached = scan_budget_reached
    for variable, choices in join_sets:
        expanded: list[dict[str, EvidenceNode]] = []
        for binding in bindings:
            for node in choices:
                joins += 1
                if joins > pattern.join_budget:
                    budget_reached = True
                    break
                actual_variable = variable
                if variable.startswith("__any__"):
                    matching_variables = [
                        candidate_variable
                        for candidate_variable in next(
                            group.variables
                            for group in any_groups
                            if variable == f"__any__{group.group_id}"
                        )
                        if node in match_sets[candidate_variable]
                    ]
                    actual_variable = matching_variables[0]
                proposed = dict(binding)
                proposed[actual_variable] = node
                if binding and not _same_scope(next(iter(binding.values())), node):
                    continue
                expanded.append(proposed)
            if budget_reached:
                break
        bindings = expanded
        if budget_reached or not bindings:
            break

    candidates: list[CandidateWindowData] = []
    used_node_ids: set[str] = set()
    used_edge_ids: set[str] = set()
    for binding in bindings:
        valid, used_edges = _binding_valid(binding, pattern, edge_map)
        if not valid or not binding:
            continue
        candidate = _candidate_from_binding(pattern, binding, used_edges)
        candidates.append(candidate)
        used_node_ids.update(node.node_id for node in binding.values())
        used_edge_ids.update(used_edges)

    logic_safeties: dict[str, ObservationSafety] = {}
    logic_decisions: dict[str, PredicateDecision] = {}
    node_constraints = {constraint.variable: constraint for constraint in pattern.nodes}
    for group in pattern.logic_constraints:
        safety_nodes = _observation_nodes(
            nodes, predicate_scope=group.group_id
        )
        safety = _observation_safety(safety_nodes)
        logic_safeties[group.group_id] = safety
        used_node_ids.update(node.node_id for node in safety_nodes)
        if group.operator in {LogicOperator.ALL, LogicOperator.ANY}:
            evidence_ids = tuple(
                dict.fromkeys(
                    node.node_id
                    for variable in group.variables
                    for node in match_sets.get(variable, ())
                )
            )
            if group.operator is LogicOperator.ANY:
                positive = bool(evidence_ids)
            else:
                required_atoms = {
                    variable.removeprefix("v_") for variable in group.variables
                }
                positive = any(
                    required_atoms.issubset(candidate.atom_ids)
                    for candidate in candidates
                )
            used_node_ids.update(evidence_ids)
            if positive:
                logic_decisions[group.group_id] = PredicateDecision(
                    PredicateState.SUPPORTED,
                    (
                        "visible_alternative_present"
                        if group.operator is LogicOperator.ANY
                        else "all_visible_terms_present"
                    ),
                    evidence_ids,
                )
            elif not safety.complete:
                logic_decisions[group.group_id] = PredicateDecision(
                    PredicateState.UNDETERMINED,
                    "incomplete_observation_coverage",
                )
            elif not safety.assessable:
                logic_decisions[group.group_id] = PredicateDecision(
                    PredicateState.UNOBSERVABLE,
                    "occlusion_or_unassessable_region",
                )
            else:
                logic_decisions[group.group_id] = PredicateDecision(
                    PredicateState.CONTRADICTED,
                    "no_visible_alternative"
                    if group.operator is LogicOperator.ANY
                    else "required_visible_term_missing",
                )
        elif group.operator is LogicOperator.VISIBLE_NONE:
            variable = group.variables[0]
            decision = evaluate_visible_none(
                target_evidence_ids=(
                    node.node_id for node in match_sets.get(variable, ())
                ),
                safety=safety,
            )
            logic_decisions[group.group_id] = decision
            used_node_ids.update(decision.evidence_ids)
            scope_candidate = _logic_scope_candidate(
                pattern, safety_nodes, group.group_id
            )
            if scope_candidate is not None:
                candidates.append(scope_candidate)
        elif group.operator is LogicOperator.COUNT:
            variable = group.variables[0]
            constraint = node_constraints[variable]
            tracklets = [
                TrackletObservation(
                    tracklet_id=node.node_id,
                    label=constraint.text,
                    video_id=node.video_id,
                    camera_id=node.camera_id,
                    t0=node.t0,
                    t1=node.t1,
                    continuity=str(node.payload.get("continuity", "continuous")),
                    fragment_group=node.payload.get("fragment_group"),
                )
                for node in nodes
                if node.node_type == "tracklet"
                and node in match_sets.get(variable, ())
            ]
            decision = evaluate_bounded_count(
                tracklets=tracklets,
                target_label=constraint.text,
                min_count=0 if group.min_count is None else group.min_count,
                max_count=8 if group.max_count is None else group.max_count,
                safety=safety,
                fragmentation_tolerance=fragmentation_tolerance,
            )
            logic_decisions[group.group_id] = decision
            used_node_ids.update(decision.evidence_ids)
            scope_candidate = _logic_scope_candidate(
                pattern, safety_nodes, group.group_id
            )
            if scope_candidate is not None:
                candidates.append(scope_candidate)

    unique = {
        candidate.candidate_id: candidate for candidate in candidates
    }
    unresolved_reasons = [
        decision.reason_code
        for decision in logic_decisions.values()
        if decision.state in {PredicateState.UNOBSERVABLE, PredicateState.UNDETERMINED}
    ]
    if budget_reached:
        unresolved_reasons.insert(0, "join_or_scan_budget_reached")
    observation_assessable: bool | None = (
        all(safety.assessable for safety in logic_safeties.values())
        if pattern.logic_constraints
        else None
    )
    return GraphExecutionResult(
        candidates=tuple(unique.values()),
        trace=GraphExecutionTraceData(
            enabled=True,
            pattern_id=pattern.pattern_id,
            tracklets_considered=sum(
                node.node_type == "tracklet" for node in nodes
            ),
            joins_completed=min(joins, pattern.join_budget),
            join_budget_reached=budget_reached,
            observation_scope_assessable=observation_assessable,
            node_ids=tuple(sorted(used_node_ids)),
            edge_ids=tuple(sorted(used_edge_ids)),
            unresolved_reason=";".join(dict.fromkeys(unresolved_reasons)) or None,
        ),
        logic_decisions=logic_decisions,
    )


def execute_sqlite(
    connection: sqlite3.Connection,
    pattern: GraphPattern,
    *,
    node_scan_budget: int | None = None,
    edge_scan_budget: int | None = None,
    fragmentation_tolerance: int = 0,
) -> GraphExecutionResult:
    nodes, edges, scan_bound = load_bounded_graph(
        connection,
        pattern,
        node_scan_budget=node_scan_budget,
        edge_scan_budget=edge_scan_budget,
    )
    return execute_pattern(
        pattern,
        nodes,
        edges,
        scan_budget_reached=scan_bound,
        fragmentation_tolerance=fragmentation_tolerance,
    )


def graph_trace_payload(trace: GraphExecutionTraceData) -> dict[str, Any]:
    return {
        "enabled": trace.enabled,
        "pattern_id": trace.pattern_id,
        "tracklets_considered": trace.tracklets_considered,
        "joins_completed": trace.joins_completed,
        "join_budget_reached": trace.join_budget_reached,
        "observation_scope_assessable": trace.observation_scope_assessable,
        "node_ids": list(trace.node_ids),
        "edge_ids": list(trace.edge_ids),
        "unresolved_reason": trace.unresolved_reason,
    }


def graph_result_payload(result: GraphExecutionResult) -> dict[str, Any]:
    return {
        "candidates": [
            candidate_window_payload(candidate) for candidate in result.candidates
        ],
        "trace": graph_trace_payload(result.trace),
        "logic_decisions": {
            group_id: {
                "state": decision.state.value,
                "reason_code": decision.reason_code,
                "evidence_ids": list(decision.evidence_ids),
                "observed_count": decision.observed_count,
            }
            for group_id, decision in result.logic_decisions.items()
        },
    }


def graph_pattern_from_payload(value: Any) -> GraphPattern:
    """Adapt the compiler/shared GraphPattern payload back to execution data."""

    raw = boundary_mapping(value)
    if "graph_pattern" in raw:
        raw = boundary_mapping(raw["graph_pattern"])
    raw_nodes = raw.get("nodes", ())
    if not raw_nodes:
        raise ValueError(
            "GraphPattern execution requires compiler-emitted node constraints"
        )
    nodes = tuple(
        NodeConstraint(
            variable=str(item["variable"]),
            atom_id=str(item["atom_id"]),
            allowed_node_types=tuple(item["allowed_node_types"]),
            text=str(item["text"]),
            camera_ids=tuple(item.get("camera_ids", ())),
            video_ids=tuple(item.get("video_ids", ())),
            start_time=item.get("start_time"),
            end_time=item.get("end_time"),
        )
        for item in (boundary_mapping(raw_node) for raw_node in raw_nodes)
    )
    edge_constraints: list[EdgeConstraint] = []
    temporal: list[TemporalRelation] = [
        TemporalRelation(
            first_atom=str(item["first_atom"]),
            relation=str(item["relation"]),
            second_atom=str(item["second_atom"]),
            max_gap_s=float(item.get("max_gap_s", 600.0)),
            same_actor_required=bool(item.get("same_actor_required", False)),
        )
        for item in (
            boundary_mapping(raw_relation)
            for raw_relation in raw.get("temporal_relations", ())
        )
    ]
    raw_predicates = raw.get("predicates", raw.get("edges", ()))
    for raw_predicate in raw_predicates:
        item = boundary_mapping(raw_predicate)
        predicate = str(item["predicate"])
        subject = str(item.get("subject_ref", item.get("subject_variable", "")))
        object_ref = str(item.get("object_ref", item.get("object_variable", "")))
        max_gap = item.get("max_gap_s")
        if (
            predicate in {"precedes", "follows"}
            and max_gap is not None
            and not raw.get("temporal_relations")
        ):
            first_atom = subject.removeprefix("v_")
            second_atom = object_ref.removeprefix("v_")
            relation = "before"
            if predicate == "follows":
                first_atom, second_atom = second_atom, first_atom
            temporal.append(
                TemporalRelation(
                    first_atom=first_atom,
                    relation=relation,
                    second_atom=second_atom,
                    max_gap_s=float(max_gap),
                )
            )
        else:
            edge_constraints.append(
                EdgeConstraint(
                    subject_variable=subject,
                    predicate=predicate,
                    object_variable=object_ref,
                    required=bool(item.get("required", True)),
                    source_relation_id=item.get(
                        "source_relation_id", item.get("predicate_id")
                    ),
                )
            )
    raw_logic = raw.get("logic_groups", raw.get("logic_constraints", ()))
    logic = tuple(
        LogicConstraint(
            group_id=str(item["group_id"]),
            operator=LogicOperator(item["operator"]),
            variables=tuple(
                item.get(
                    "variables",
                    tuple(f"v_{atom_id}" for atom_id in item.get("atom_ids", ())),
                )
            ),
            observation_scope=str(
                item.get("observation_scope", "candidate_episode")
            ),
            min_count=item.get("min_count"),
            max_count=item.get("max_count"),
        )
        for item in (boundary_mapping(raw_group) for raw_group in raw_logic)
    )
    return GraphPattern(
        pattern_id=str(raw["pattern_id"]),
        nodes=nodes,
        edges=tuple(edge_constraints),
        temporal_relations=tuple(temporal),
        logic_constraints=logic,
        verifier_relation_ids=tuple(raw.get("verifier_relation_ids", ())),
        join_budget=int(raw.get("join_budget", 10_000)),
        schema_version=str(raw.get("schema_version", "1.0.0")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute a compiled graph fixture")
    parser.add_argument("database")
    parser.add_argument("compiled_pattern_json")
    args = parser.parse_args(argv)
    with open(args.compiled_pattern_json, encoding="utf-8") as source:
        raw = json.load(source)
    pattern = graph_pattern_from_payload(raw)
    connection = sqlite3.connect(args.database)
    try:
        result = execute_sqlite(connection, pattern)
    finally:
        connection.close()
    print(json.dumps(graph_result_payload(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
