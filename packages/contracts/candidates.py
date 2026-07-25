"""Retrieval, graph execution, clustering, and assembly contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import ContractModel


class EvidenceRef(ContractModel):
    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    camera_id: str | None = None
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    frame_ids: list[int] = Field(default_factory=list)
    producer_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "EvidenceRef":
        if self.t1 < self.t0:
            raise ValueError("t1 cannot precede t0")
        return self


class CandidateWindow(ContractModel):
    candidate_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    camera_id: str | None = None
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    channel_scores: dict[str, float] = Field(default_factory=dict)
    qualifying_channels: list[str] = Field(min_length=1)
    rrf_score: float | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> "CandidateWindow":
        if self.t1 <= self.t0:
            raise ValueError("candidate t1 must be greater than t0")
        missing = set(self.qualifying_channels).difference(self.channel_scores)
        if missing:
            raise ValueError(f"qualifying channels require scores: {sorted(missing)}")
        return self


class CandidateCluster(ContractModel):
    cluster_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    camera_id: str | None = None
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    member_candidate_ids: list[str] = Field(min_length=1)
    priority_score: float = 0.0
    evidence: list[EvidenceRef] = Field(default_factory=list)


class GraphExecutionTrace(ContractModel):
    enabled: bool = False
    pattern_id: str | None = None
    tracklets_considered: int = Field(default=0, ge=0)
    joins_completed: int = Field(default=0, ge=0)
    join_budget_reached: bool = False
    observation_scope_assessable: bool | None = None
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None


class AssemblyCompleteness(ContractModel):
    anchor_candidates_qualifying: int = Field(default=0, ge=0)
    anchor_candidates_retained: int = Field(default=0, ge=0)
    episodes_generated: int = Field(default=0, ge=0)
    episode_cap_bound: bool = False
    assembly_complete: bool = True

    @model_validator(mode="after")
    def prevent_false_completeness(self) -> "AssemblyCompleteness":
        if self.anchor_candidates_retained < self.anchor_candidates_qualifying:
            self.assembly_complete = False
        if self.episode_cap_bound:
            self.assembly_complete = False
        return self


class CandidateSet(ContractModel):
    search_id: str = Field(min_length=1)
    query_plan_version: str = Field(min_length=1)
    channels_run: list[str] = Field(default_factory=list)
    candidate_recall_operating_point: str = Field(default="not_yet_measured", min_length=1)
    exact_scoring_completed: bool
    windows: list[CandidateWindow] = Field(default_factory=list)
    clusters: list[CandidateCluster] = Field(default_factory=list)
    graph_execution: GraphExecutionTrace = Field(default_factory=GraphExecutionTrace)
    assembly: AssemblyCompleteness = Field(default_factory=AssemblyCompleteness)
    verification_budget_clusters: int | None = Field(default=None, ge=0)
