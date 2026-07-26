"""Compile validated query plans to fixed graph patterns and recall channels."""

from __future__ import annotations

import argparse
import json
from typing import Any

from evidence.predicates import resolve_edge_predicate
from query.schema import (
    AtomRole,
    AtomType,
    CompiledQuery,
    EdgeConstraint,
    GraphPattern,
    InterpretationState,
    LogicConstraint,
    NodeConstraint,
    QueryPlanData,
    QueryValidationError,
    SemanticChannel,
    canonical_data,
    coerce_query_plan,
    stable_identifier,
    unique_preserving_order,
    validate_query_plan,
    query_plan_payload,
)


NODE_TYPES_BY_ATOM: dict[AtomType, tuple[str, ...]] = {
    AtomType.OBJECT: ("detection", "tracklet", "frame", "window"),
    AtomType.ATTRIBUTE: ("detection", "tracklet", "frame", "window"),
    AtomType.ACTION: ("tracklet", "window", "clip", "episode"),
    AtomType.LOCATION: ("detection", "tracklet", "window", "episode"),
    AtomType.RELATION: ("window", "episode"),
    AtomType.TEMPORAL: ("window", "episode"),
    AtomType.SCENE: ("frame", "window", "clip", "episode"),
}


def compile_query(
    value: QueryPlanData | Any,
    *,
    join_budget: int = 10_000,
    enable_graph: bool = True,
    enable_clip: bool = False,
    enable_ocr: bool = False,
    enable_asr: bool = False,
) -> CompiledQuery:
    """Compile data, never text, into a bounded, non-executable pattern."""

    plan = coerce_query_plan(value)
    validate_query_plan(plan)
    if plan.interpretation_state is InterpretationState.CLARIFICATION_REQUIRED:
        raise QueryValidationError("clarification is required before compilation")
    if not 0 < join_budget <= 1_000_000:
        raise QueryValidationError("join_budget must be in 1..1,000,000")

    nodes = tuple(
        NodeConstraint(
            variable=f"v_{atom.atom_id}",
            atom_id=atom.atom_id,
            allowed_node_types=NODE_TYPES_BY_ATOM[atom.type],
            text=atom.text_span,
            camera_ids=plan.filters.camera_ids,
            video_ids=plan.filters.video_ids,
            start_time=plan.filters.start_time,
            end_time=plan.filters.end_time,
        )
        for atom in plan.atoms
    )
    edge_constraints: list[EdgeConstraint] = []
    verifier_relations: list[str] = []
    for relation in plan.relations:
        storage_predicate = resolve_edge_predicate(relation.predicate)
        if storage_predicate is None:
            verifier_relations.append(relation.relation_id)
            continue
        edge_constraints.append(
            EdgeConstraint(
                subject_variable=f"v_{relation.subject_atom}",
                predicate=storage_predicate,
                object_variable=f"v_{relation.object_atom}",
                required=relation.required,
                source_relation_id=relation.relation_id,
            )
        )

    logic_constraints = tuple(
        LogicConstraint(
            group_id=group.group_id,
            operator=group.operator,
            variables=tuple(f"v_{atom_id}" for atom_id in group.atom_ids),
            observation_scope=group.observation_scope,
            min_count=group.min_count,
            max_count=group.max_count,
        )
        for group in plan.logic_groups
    )
    pattern_seed = {
        "nodes": nodes,
        "edges": edge_constraints,
        "temporal_relations": plan.temporal_relations,
        "logic_constraints": logic_constraints,
        "verifier_relation_ids": verifier_relations,
        "filters": plan.filters,
        "join_budget": join_budget,
    }
    pattern = GraphPattern(
        pattern_id=stable_identifier("gp", pattern_seed),
        nodes=nodes,
        edges=tuple(edge_constraints),
        temporal_relations=plan.temporal_relations,
        logic_constraints=logic_constraints,
        verifier_relation_ids=tuple(verifier_relations),
        join_budget=join_budget,
    )

    channels: list[SemanticChannel] = [
        SemanticChannel(
            channel_id="frame:whole_query",
            text=plan.query_text,
            kind="whole_query_frame",
            atom_ids=tuple(atom.atom_id for atom in plan.atoms),
            threshold_key="whole_query_frame",
        )
    ]
    for atom in plan.atoms:
        if atom.role is AtomRole.CANDIDATE_ANCHOR:
            kind = (
                "rare_attribute_frame"
                if atom.type is AtomType.ATTRIBUTE
                else "candidate_anchor_frame"
            )
            channels.append(
                SemanticChannel(
                    channel_id=f"frame:atom:{atom.atom_id}",
                    text=atom.text_span,
                    kind=kind,
                    atom_ids=(atom.atom_id,),
                    threshold_key=kind,
                )
            )
    if enable_clip and any(
        atom.type in {AtomType.ACTION, AtomType.TEMPORAL} for atom in plan.atoms
    ):
        channels.append(
            SemanticChannel(
                channel_id="clip:whole_query",
                text=plan.query_text,
                kind="native_clip",
                atom_ids=tuple(atom.atom_id for atom in plan.atoms),
                threshold_key="native_clip",
            )
        )
    if enable_graph:
        graph_atoms = tuple(
            atom.atom_id
            for atom in plan.atoms
            if atom.type in {AtomType.OBJECT, AtomType.ACTION, AtomType.LOCATION}
        )
        if graph_atoms or plan.relations or plan.logic_groups:
            channels.append(
                SemanticChannel(
                    channel_id="graph:pattern",
                    text=plan.query_text,
                    kind="temporal_graph",
                    atom_ids=graph_atoms,
                    threshold_key="graph_pattern",
                )
            )
    if enable_ocr:
        channels.append(
            SemanticChannel(
                channel_id="ocr:exact",
                text=plan.query_text,
                kind="ocr",
                threshold_key="ocr",
            )
        )
    if enable_asr:
        channels.append(
            SemanticChannel(
                channel_id="asr:semantic",
                text=plan.query_text,
                kind="asr",
                threshold_key="asr",
            )
        )
    if (
        plan.filters.video_ids
        or plan.filters.camera_ids
        or plan.filters.zone_ids
        or plan.filters.start_time is not None
        or plan.filters.end_time is not None
    ):
        channels.append(
            SemanticChannel(
                channel_id="filter:scope",
                text="",
                kind="filter",
                threshold_key="filter",
            )
        )

    channel_ids = [channel.channel_id for channel in channels]
    if len(channel_ids) != len(set(channel_ids)):
        raise AssertionError("compiler generated duplicate channel IDs")
    return CompiledQuery(plan, pattern, tuple(channels))


def graph_pattern_payload(compiled: CompiledQuery | GraphPattern) -> dict[str, Any]:
    """Emit a payload compatible with ``packages.contracts.GraphPattern``."""

    if isinstance(compiled, CompiledQuery):
        pattern = compiled.graph_pattern
        plan = compiled.query_plan
    else:
        pattern = compiled
        plan = None
    predicates: list[dict[str, Any]] = []
    for index, edge in enumerate(pattern.edges, start=1):
        predicates.append(
            {
                "predicate_id": edge.source_relation_id or f"edge_{index}",
                "subject_ref": edge.subject_variable,
                "predicate": edge.predicate,
                "object_ref": edge.object_variable,
                "required": edge.required,
            }
        )
    for index, temporal in enumerate(pattern.temporal_relations, start=1):
        first = f"v_{temporal.first_atom}"
        second = f"v_{temporal.second_atom}"
        if temporal.relation == "after":
            first, second = second, first
        predicates.append(
            {
                "predicate_id": f"temporal_{index}",
                "subject_ref": first,
                "predicate": "precedes",
                "object_ref": second,
                "required": True,
                "max_gap_s": temporal.max_gap_s,
                # `precedes` is also a legitimate stored edge predicate
                # (evidence/predicates.py EDGE_PREDICATES, and the evidence_edges
                # CHECK in GRAPH_SCHEMA), so a reader cannot tell this duplicate
                # of temporal_relations apart from a real graph edge by predicate
                # name alone. Marking the origin lets the reader drop the
                # duplicate without closing off the genuine edge form.
                "derived_from": "temporal_relation",
                # Carried so a consumer reconstructing from predicates alone does
                # not silently widen a same-actor constraint into order-only.
                "same_actor_required": temporal.same_actor_required,
            }
        )
    return {
        "schema_version": pattern.schema_version,
        "pattern_id": pattern.pattern_id,
        "query_plan_version": plan.schema_version if plan else pattern.schema_version,
        "predicates": predicates,
        "logic_groups": [
            {
                "group_id": item.group_id,
                "operator": item.operator.value,
                "atom_ids": [
                    variable.removeprefix("v_") for variable in item.variables
                ],
                "observation_scope": item.observation_scope,
                "min_count": item.min_count,
                "max_count": item.max_count,
            }
            for item in pattern.logic_constraints
        ],
        "camera_ids": list(plan.filters.camera_ids) if plan else [],
        "start_time": plan.filters.start_time if plan else None,
        "end_time": plan.filters.end_time if plan else None,
        "join_budget": pattern.join_budget,
        "fixed_predicate_registry_version": "v1",
        # Forward-compatible execution metadata.  It is data only.
        "nodes": canonical_data(pattern.nodes),
        "temporal_relations": canonical_data(pattern.temporal_relations),
        "verifier_relation_ids": list(pattern.verifier_relation_ids),
    }


def compiled_query_payload(compiled: CompiledQuery) -> dict[str, Any]:
    return {
        "query_plan": query_plan_payload(compiled.query_plan),
        "graph_pattern": graph_pattern_payload(compiled),
        "semantic_channels": canonical_data(compiled.semantic_channels),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile a validated query plan JSON")
    parser.add_argument("plan_json")
    parser.add_argument("--join-budget", type=int, default=10_000)
    parser.add_argument("--clip", action="store_true")
    args = parser.parse_args(argv)
    with open(args.plan_json, encoding="utf-8") as source:
        plan = json.load(source)
    result = compile_query(plan, join_budget=args.join_budget, enable_clip=args.clip)
    print(json.dumps(compiled_query_payload(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
