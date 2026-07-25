"""Natural-language interpretation and bounded graph-pattern contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .base import ContractModel


class InterpretationState(StrEnum):
    CLEAR = "clear"
    CLARIFICATION_REQUIRED = "clarification_required"
    PARSER_FALLBACK = "parser_fallback"


class AtomType(StrEnum):
    OBJECT = "object"
    ATTRIBUTE = "attribute"
    ACTION = "action"
    LOCATION = "location"
    RELATION = "relation"
    TEMPORAL = "temporal"
    SCENE = "scene"


class AtomRole(StrEnum):
    CANDIDATE_ANCHOR = "candidate_anchor"
    VERIFIER_ONLY = "verifier_only"
    FILTER = "filter"
    WEAK_CONTEXT = "weak_context"


class Atom(ContractModel):
    atom_id: str = Field(min_length=1)
    text_span: str = Field(min_length=1)
    type: AtomType
    required: bool = True
    visibility_sensitive: bool = True
    role: AtomRole = AtomRole.CANDIDATE_ANCHOR


class QueryRelation(ContractModel):
    relation_id: str = Field(min_length=1)
    subject_atom: str = Field(min_length=1)
    predicate: str = Field(pattern=r"^(carries|near|wears|places|picks_up|follows)$")
    object_atom: str = Field(min_length=1)
    required: bool = True


class TemporalRelation(ContractModel):
    first_atom: str = Field(min_length=1)
    relation: str = Field(pattern=r"^(before|after)$")
    second_atom: str = Field(min_length=1)
    max_gap_s: float = Field(default=600, gt=0)
    same_actor_required: bool = False


class LogicOperator(StrEnum):
    ALL = "all"
    ANY = "any"
    VISIBLE_NONE = "visible_none"
    COUNT = "count"


class LogicGroup(ContractModel):
    group_id: str = Field(min_length=1)
    operator: LogicOperator
    atom_ids: list[str] = Field(min_length=1, max_length=8)
    observation_scope: str = Field(default="candidate_episode", min_length=1)
    min_count: int | None = Field(default=None, ge=0, le=8)
    max_count: int | None = Field(default=None, ge=0, le=8)

    @model_validator(mode="after")
    def validate_bounds(self) -> "LogicGroup":
        if self.operator == LogicOperator.COUNT:
            if self.min_count is None and self.max_count is None:
                raise ValueError("count requires min_count or max_count")
            if (
                self.min_count is not None
                and self.max_count is not None
                and self.min_count > self.max_count
            ):
                raise ValueError("min_count cannot exceed max_count")
        elif self.min_count is not None or self.max_count is not None:
            raise ValueError("count bounds are legal only for the count operator")
        if self.operator == LogicOperator.VISIBLE_NONE and not self.observation_scope:
            raise ValueError("visible_none requires an observation_scope")
        return self


class QueryFilters(ContractModel):
    video_ids: list[str] = Field(default_factory=list)
    camera_ids: list[str] = Field(default_factory=list)
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    zone_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_scope(self) -> "QueryFilters":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("end_time must be greater than start_time")
        return self


class QueryPlan(ContractModel):
    query_text: str = Field(min_length=1)
    state: InterpretationState = InterpretationState.CLEAR
    atoms: list[Atom] = Field(default_factory=list)
    relations: list[QueryRelation] = Field(default_factory=list)
    temporal_relations: list[TemporalRelation] = Field(default_factory=list)
    logic_groups: list[LogicGroup] = Field(default_factory=list)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    ambiguities: list[str] = Field(default_factory=list)
    unsupported_constructs: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    parser_version: str = Field(default="deterministic-v1", min_length=1)

    @model_validator(mode="after")
    def validate_interpretation(self) -> "QueryPlan":
        atom_ids = [atom.atom_id for atom in self.atoms]
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("atom_id values must be unique")
        known = set(atom_ids)
        for relation in self.relations:
            if relation.subject_atom not in known or relation.object_atom not in known:
                raise ValueError("relations must reference known atoms")
        for relation in self.temporal_relations:
            if relation.first_atom not in known or relation.second_atom not in known:
                raise ValueError("temporal relations must reference known atoms")
            if relation.same_actor_required and relation.max_gap_s > 30:
                raise ValueError("long-gap same-actor requests must route to clarification")
        for group in self.logic_groups:
            if not set(group.atom_ids).issubset(known):
                raise ValueError("logic groups must reference known atoms")
        if self.state == InterpretationState.CLARIFICATION_REQUIRED and not self.clarification_question:
            raise ValueError("clarification_required plans require one clarification_question")
        return self


class GraphPredicate(ContractModel):
    predicate_id: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    predicate: str = Field(
        pattern=r"^(overlaps|precedes|follows|near|contains|belongs_to_track|carries|enters|exits|co_occurs)$"
    )
    object_ref: str = Field(min_length=1)
    required: bool = True
    max_gap_s: float | None = Field(default=None, gt=0)


class GraphPattern(ContractModel):
    pattern_id: str = Field(min_length=1)
    query_plan_version: str = Field(min_length=1)
    predicates: list[GraphPredicate] = Field(default_factory=list)
    logic_groups: list[LogicGroup] = Field(default_factory=list)
    camera_ids: list[str] = Field(default_factory=list)
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    join_budget: int = Field(default=10_000, gt=0, le=1_000_000)
    fixed_predicate_registry_version: str = Field(default="v1", min_length=1)
