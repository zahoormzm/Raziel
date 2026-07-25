"""Query-intelligence schemas and contract adapters.

The shared ``packages.contracts`` package owns wire contracts.  This module
deliberately keeps an internal, standard-library representation and accepts
plain mappings, dataclasses, and Pydantic v1/v2 models at the boundary.  That
lets the query lane run in CPU-only tests without taking ownership of the
shared contract package.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0.0"
MAX_ANY_ALTERNATIVES = 4
MAX_COUNT_BOUND = 8
MAX_TEMPORAL_GAP_S = 600.0


class QueryValidationError(ValueError):
    """Raised when a query would escape the supported, bounded algebra."""


class AtomType(str, Enum):
    OBJECT = "object"
    ATTRIBUTE = "attribute"
    ACTION = "action"
    LOCATION = "location"
    RELATION = "relation"
    TEMPORAL = "temporal"
    SCENE = "scene"


class AtomRole(str, Enum):
    CANDIDATE_ANCHOR = "candidate_anchor"
    VERIFIER_ONLY = "verifier_only"
    FILTER = "filter"
    WEAK_CONTEXT = "weak_context"


class LogicOperator(str, Enum):
    ALL = "all"
    ANY = "any"
    VISIBLE_NONE = "visible_none"
    COUNT = "count"


class InterpretationState(str, Enum):
    CLEAR = "clear"
    CLARIFICATION_REQUIRED = "clarification_required"
    PARSER_FALLBACK = "parser_fallback"


class PredicateState(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNOBSERVABLE = "unobservable"
    UNDETERMINED = "undetermined"


SUPPORTED_RELATION_PREDICATES = frozenset(
    {"carries", "near", "wears", "places", "picks_up", "follows"}
)
SUPPORTED_TEMPORAL_RELATIONS = frozenset({"before", "after"})
SUPPORTED_OBSERVATION_SCOPES = frozenset(
    {"candidate_episode", "camera_time_interval"}
)


@dataclass(frozen=True)
class QueryAtom:
    atom_id: str
    text_span: str
    type: AtomType
    required: bool = True
    visibility_sensitive: bool = True
    role: AtomRole = AtomRole.CANDIDATE_ANCHOR


@dataclass(frozen=True)
class QueryRelation:
    relation_id: str
    subject_atom: str
    predicate: str
    object_atom: str
    required: bool = True


@dataclass(frozen=True)
class TemporalRelation:
    first_atom: str
    relation: str
    second_atom: str
    max_gap_s: float = MAX_TEMPORAL_GAP_S
    same_actor_required: bool = False


@dataclass(frozen=True)
class LogicGroup:
    group_id: str
    operator: LogicOperator
    atom_ids: tuple[str, ...]
    observation_scope: str = "candidate_episode"
    min_count: int | None = None
    max_count: int | None = None


@dataclass(frozen=True)
class QueryFilters:
    video_ids: tuple[str, ...] = ()
    camera_ids: tuple[str, ...] = ()
    start_time: float | None = None
    end_time: float | None = None
    zone_ids: tuple[str, ...] = ()
    sampling_policy_version: str | None = None


@dataclass(frozen=True)
class QueryPlanData:
    query_text: str
    atoms: tuple[QueryAtom, ...]
    relations: tuple[QueryRelation, ...] = ()
    temporal_relations: tuple[TemporalRelation, ...] = ()
    logic_groups: tuple[LogicGroup, ...] = ()
    filters: QueryFilters = field(default_factory=QueryFilters)
    ambiguities: tuple[str, ...] = ()
    unsupported_constructs: tuple[str, ...] = ()
    clarification_question: str | None = None
    interpretation_state: InterpretationState = InterpretationState.CLEAR
    parser_version: str = "deterministic-v1"
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class SemanticChannel:
    channel_id: str
    text: str
    kind: str
    atom_ids: tuple[str, ...] = ()
    threshold_key: str = "default"
    enabled: bool = True


@dataclass(frozen=True)
class NodeConstraint:
    variable: str
    atom_id: str
    allowed_node_types: tuple[str, ...]
    text: str
    camera_ids: tuple[str, ...] = ()
    video_ids: tuple[str, ...] = ()
    start_time: float | None = None
    end_time: float | None = None


@dataclass(frozen=True)
class EdgeConstraint:
    subject_variable: str
    predicate: str
    object_variable: str
    required: bool = True
    source_relation_id: str | None = None


@dataclass(frozen=True)
class LogicConstraint:
    group_id: str
    operator: LogicOperator
    variables: tuple[str, ...]
    observation_scope: str
    min_count: int | None = None
    max_count: int | None = None


@dataclass(frozen=True)
class GraphPattern:
    pattern_id: str
    nodes: tuple[NodeConstraint, ...]
    edges: tuple[EdgeConstraint, ...] = ()
    temporal_relations: tuple[TemporalRelation, ...] = ()
    logic_constraints: tuple[LogicConstraint, ...] = ()
    verifier_relation_ids: tuple[str, ...] = ()
    join_budget: int = 10_000
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class CompiledQuery:
    query_plan: QueryPlanData
    graph_pattern: GraphPattern
    semantic_channels: tuple[SemanticChannel, ...]


@dataclass(frozen=True)
class EvidenceRefData:
    node_id: str
    node_type: str
    video_id: str
    camera_id: str | None
    t0: float
    t1: float
    frame_ids: tuple[int, ...] = ()
    producer_version: str = "query-intelligence-v1"


@dataclass(frozen=True)
class CandidateWindowData:
    candidate_id: str
    video_id: str
    camera_id: str | None
    t0: float
    t1: float
    channel_scores: Mapping[str, float]
    qualifying_channels: tuple[str, ...]
    rrf_score: float | None = None
    evidence: tuple[EvidenceRefData, ...] = ()
    atom_ids: tuple[str, ...] = ()
    tracklet_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateClusterData:
    cluster_id: str
    video_id: str
    camera_id: str | None
    t0: float
    t1: float
    member_candidate_ids: tuple[str, ...]
    priority_score: float = 0.0
    evidence: tuple[EvidenceRefData, ...] = ()


def coerce_candidate_window(value: Any) -> CandidateWindowData:
    if isinstance(value, CandidateWindowData):
        return value
    raw = boundary_mapping(value)
    evidence: list[EvidenceRefData] = []
    for item in raw.get("evidence", ()):
        ref = boundary_mapping(item)
        evidence.append(
            EvidenceRefData(
                node_id=str(ref["node_id"]),
                node_type=str(ref["node_type"]),
                video_id=str(ref["video_id"]),
                camera_id=ref.get("camera_id"),
                t0=float(ref["t0"]),
                t1=float(ref["t1"]),
                frame_ids=tuple(int(v) for v in ref.get("frame_ids", ())),
                producer_version=str(
                    ref.get("producer_version", "query-intelligence-v1")
                ),
            )
        )
    scores = {
        str(key): float(score)
        for key, score in dict(raw.get("channel_scores", {})).items()
    }
    qualifying = _tuple_strings(
        raw.get("qualifying_channels", ()), "qualifying_channels"
    )
    if not qualifying or not set(qualifying).issubset(scores):
        raise QueryValidationError(
            "candidate qualifying_channels must be non-empty and have scores"
        )
    t0, t1 = float(raw["t0"]), float(raw["t1"])
    if t1 <= t0:
        raise QueryValidationError("candidate t1 must be greater than t0")
    return CandidateWindowData(
        candidate_id=str(raw["candidate_id"]),
        video_id=str(raw["video_id"]),
        camera_id=raw.get("camera_id"),
        t0=t0,
        t1=t1,
        channel_scores=scores,
        qualifying_channels=qualifying,
        rrf_score=(
            None if raw.get("rrf_score") is None else float(raw["rrf_score"])
        ),
        evidence=tuple(evidence),
        atom_ids=_tuple_strings(raw.get("atom_ids", ()), "atom_ids"),
        tracklet_ids=_tuple_strings(raw.get("tracklet_ids", ()), "tracklet_ids"),
        trace_ids=_tuple_strings(raw.get("trace_ids", ()), "trace_ids"),
    )


def candidate_window_payload(value: CandidateWindowData) -> dict[str, Any]:
    payload = canonical_data(value)
    # ContractModel accepts the extra atom/track/trace metadata.
    return payload


def boundary_mapping(value: Any) -> dict[str, Any]:
    """Convert a shared contract/Pydantic/dataclass boundary to a mapping."""

    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        return dict(legacy_dict())
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Expected mapping, dataclass, or Pydantic model; got {type(value)!r}")


def _tuple_strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise QueryValidationError(f"{field_name} must be a list of strings")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result):
        raise QueryValidationError(f"{field_name} contains an empty value")
    return result


def _enum(enum_type: type[Enum], value: Any, field_name: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise QueryValidationError(f"{field_name} must be one of: {allowed}") from exc


def _query_filters(value: Any) -> QueryFilters:
    raw = boundary_mapping(value or {})
    start = raw.get("start_time", raw.get("start_time_s"))
    end = raw.get("end_time", raw.get("end_time_s"))
    start_f = None if start is None else float(start)
    end_f = None if end is None else float(end)
    if start_f is not None and end_f is not None and end_f <= start_f:
        raise QueryValidationError("filters.end_time must be greater than start_time")
    return QueryFilters(
        video_ids=_tuple_strings(raw.get("video_ids", ()), "filters.video_ids"),
        camera_ids=_tuple_strings(raw.get("camera_ids", ()), "filters.camera_ids"),
        start_time=start_f,
        end_time=end_f,
        zone_ids=_tuple_strings(raw.get("zone_ids", ()), "filters.zone_ids"),
        sampling_policy_version=raw.get("sampling_policy_version"),
    )


def coerce_query_filters(value: Any) -> QueryFilters:
    if isinstance(value, QueryFilters):
        return value
    return _query_filters(value)


def coerce_query_plan(value: Any, *, query_text: str | None = None) -> QueryPlanData:
    """Validate a shared QueryPlan-like value into the internal schema."""

    if isinstance(value, QueryPlanData):
        validate_query_plan(value)
        return value
    raw = boundary_mapping(value)
    raw_atoms = raw.get("atoms", ())
    if not isinstance(raw_atoms, Sequence) or isinstance(raw_atoms, str):
        raise QueryValidationError("atoms must be a list")
    atoms: list[QueryAtom] = []
    for index, item in enumerate(raw_atoms):
        atom = boundary_mapping(item)
        atom_id = str(atom.get("atom_id", "")).strip()
        text_span = str(atom.get("text_span", "")).strip()
        if not atom_id or not text_span:
            raise QueryValidationError(f"atoms[{index}] requires atom_id and text_span")
        atoms.append(
            QueryAtom(
                atom_id=atom_id,
                text_span=text_span,
                type=_enum(AtomType, atom.get("type"), f"atoms[{index}].type"),
                required=bool(atom.get("required", True)),
                visibility_sensitive=bool(atom.get("visibility_sensitive", True)),
                role=_enum(
                    AtomRole,
                    atom.get("role", AtomRole.CANDIDATE_ANCHOR.value),
                    f"atoms[{index}].role",
                ),
            )
        )

    relations: list[QueryRelation] = []
    for index, item in enumerate(raw.get("relations", ())):
        relation = boundary_mapping(item)
        predicate = str(relation.get("predicate", "")).strip()
        if predicate not in SUPPORTED_RELATION_PREDICATES:
            raise QueryValidationError(
                f"relations[{index}].predicate is unsupported: {predicate!r}"
            )
        relations.append(
            QueryRelation(
                relation_id=str(relation.get("relation_id", f"r{index + 1}")),
                subject_atom=str(relation.get("subject_atom", "")),
                predicate=predicate,
                object_atom=str(relation.get("object_atom", "")),
                required=bool(relation.get("required", True)),
            )
        )

    temporal: list[TemporalRelation] = []
    for index, item in enumerate(raw.get("temporal_relations", ())):
        relation = boundary_mapping(item)
        rel = str(relation.get("relation", "")).strip()
        if rel not in SUPPORTED_TEMPORAL_RELATIONS:
            raise QueryValidationError(
                f"temporal_relations[{index}].relation is unsupported: {rel!r}"
            )
        max_gap = float(relation.get("max_gap_s", MAX_TEMPORAL_GAP_S))
        if not 0 < max_gap <= MAX_TEMPORAL_GAP_S:
            raise QueryValidationError(
                f"temporal_relations[{index}].max_gap_s must be in "
                f"(0, {MAX_TEMPORAL_GAP_S}]"
            )
        temporal.append(
            TemporalRelation(
                first_atom=str(relation.get("first_atom", "")),
                relation=rel,
                second_atom=str(relation.get("second_atom", "")),
                max_gap_s=max_gap,
                same_actor_required=bool(relation.get("same_actor_required", False)),
            )
        )

    logic: list[LogicGroup] = []
    for index, item in enumerate(raw.get("logic_groups", ())):
        group = boundary_mapping(item)
        operator = _enum(
            LogicOperator, group.get("operator"), f"logic_groups[{index}].operator"
        )
        logic.append(
            LogicGroup(
                group_id=str(group.get("group_id", f"g{index + 1}")),
                operator=operator,
                atom_ids=_tuple_strings(
                    group.get("atom_ids", ()), f"logic_groups[{index}].atom_ids"
                ),
                observation_scope=str(
                    group.get("observation_scope", "candidate_episode")
                ),
                min_count=(
                    None
                    if group.get("min_count") is None
                    else int(group.get("min_count"))
                ),
                max_count=(
                    None
                    if group.get("max_count") is None
                    else int(group.get("max_count"))
                ),
            )
        )

    state_raw = raw.get("interpretation_state", raw.get("state", "clear"))
    plan = QueryPlanData(
        query_text=str(query_text if query_text is not None else raw.get("query_text", "")),
        atoms=tuple(atoms),
        relations=tuple(relations),
        temporal_relations=tuple(temporal),
        logic_groups=tuple(logic),
        filters=_query_filters(raw.get("filters", {})),
        ambiguities=_tuple_strings(raw.get("ambiguities", ()), "ambiguities"),
        unsupported_constructs=_tuple_strings(
            raw.get("unsupported_constructs", ()), "unsupported_constructs"
        ),
        clarification_question=raw.get("clarification_question"),
        interpretation_state=_enum(
            InterpretationState, state_raw, "interpretation_state"
        ),
        parser_version=str(raw.get("parser_version", "deterministic-v1")),
        schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
    )
    validate_query_plan(plan)
    return plan


def validate_query_plan(plan: QueryPlanData) -> None:
    atom_ids = [atom.atom_id for atom in plan.atoms]
    if len(atom_ids) != len(set(atom_ids)):
        raise QueryValidationError("atom_id values must be unique")
    known = set(atom_ids)
    if plan.interpretation_state is not InterpretationState.CLARIFICATION_REQUIRED:
        if not plan.query_text.strip():
            raise QueryValidationError("query_text cannot be empty")
        if not plan.atoms:
            raise QueryValidationError("a clear/fallback query requires at least one atom")
    for relation in plan.relations:
        if relation.subject_atom not in known or relation.object_atom not in known:
            raise QueryValidationError(
                f"relation {relation.relation_id} references an unknown atom"
            )
        if relation.subject_atom == relation.object_atom:
            raise QueryValidationError(
                f"relation {relation.relation_id} cannot self-reference"
            )
    for relation in plan.temporal_relations:
        if relation.first_atom not in known or relation.second_atom not in known:
            raise QueryValidationError("temporal relation references an unknown atom")
        if relation.first_atom == relation.second_atom:
            raise QueryValidationError("temporal relation cannot self-reference")
        if relation.same_actor_required and relation.max_gap_s > 30:
            raise QueryValidationError(
                "same-actor binding is unsafe across gaps above 30 seconds; "
                "request order-only reasoning instead"
            )
    group_ids: set[str] = set()
    for group in plan.logic_groups:
        if group.group_id in group_ids:
            raise QueryValidationError("logic group IDs must be unique")
        group_ids.add(group.group_id)
        if not group.atom_ids or not set(group.atom_ids).issubset(known):
            raise QueryValidationError(
                f"logic group {group.group_id} references unknown/no atoms"
            )
        if group.observation_scope not in SUPPORTED_OBSERVATION_SCOPES:
            raise QueryValidationError(
                f"logic group {group.group_id} requires an explicit bounded "
                "candidate_episode or camera_time_interval scope"
            )
        if group.operator is LogicOperator.ANY:
            if len(group.atom_ids) < 2 or len(group.atom_ids) > MAX_ANY_ALTERNATIVES:
                raise QueryValidationError(
                    f"bounded any requires 2..{MAX_ANY_ALTERNATIVES} alternatives"
                )
        elif group.operator is LogicOperator.VISIBLE_NONE:
            if len(group.atom_ids) != 1:
                raise QueryValidationError("visible_none accepts exactly one target atom")
        elif group.operator is LogicOperator.COUNT:
            if len(group.atom_ids) != 1:
                raise QueryValidationError("count accepts exactly one tracklet class atom")
            low = 0 if group.min_count is None else group.min_count
            high = MAX_COUNT_BOUND if group.max_count is None else group.max_count
            if low < 0 or high < low or high > MAX_COUNT_BOUND:
                raise QueryValidationError(
                    f"count bounds must satisfy 0 <= min <= max <= {MAX_COUNT_BOUND}"
                )
        elif group.min_count is not None or group.max_count is not None:
            raise QueryValidationError(
                f"{group.operator.value} does not accept count bounds"
            )
    if plan.unsupported_constructs:
        if plan.interpretation_state is not InterpretationState.CLARIFICATION_REQUIRED:
            raise QueryValidationError(
                "unsupported constructs must route to clarification_required"
            )
        if not plan.clarification_question:
            raise QueryValidationError(
                "clarification_required needs one focused clarification question"
            )


def canonical_data(value: Any) -> Any:
    """Return deterministic, JSON-compatible data for hashing and boundaries."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: canonical_data(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): canonical_data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_data(item) for item in value]
    return value


def stable_identifier(prefix: str, value: Any) -> str:
    payload = json.dumps(
        canonical_data(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return f"{prefix}_{sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def query_plan_payload(plan: QueryPlanData) -> dict[str, Any]:
    validate_query_plan(plan)
    payload = canonical_data(plan)
    payload["state"] = payload.pop("interpretation_state")
    payload["parser_version"] = plan.parser_version
    return payload


def atoms_by_id(plan: QueryPlanData) -> dict[str, QueryAtom]:
    return {atom.atom_id: atom for atom in plan.atoms}


def unique_preserving_order(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
