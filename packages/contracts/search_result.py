"""Final search and extraction contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, utc_now
from .query_plan import QueryPlan
from .verification import ConstraintEvidence


class ArchiveConclusion(StrEnum):
    VERIFIED_MATCHES_FOUND = "verified_matches_found"
    NO_VERIFIED_MATCH = "no_verified_match_at_operating_point"
    INSUFFICIENT_VISUAL_EVIDENCE = "insufficient_visual_evidence"
    SEARCH_INCOMPLETE = "search_incomplete"
    NOT_APPLICABLE = "not_applicable"


class IndexingSummary(ContractModel):
    expected_ticks: int = Field(ge=0)
    embedded_ticks: int = Field(ge=0)
    decode_failed_ticks: int = Field(ge=0)
    skipped_ticks: int = Field(ge=0)
    scored_coverage: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_counts(self) -> "IndexingSummary":
        if self.embedded_ticks + self.decode_failed_ticks + self.skipped_ticks != self.expected_ticks:
            raise ValueError("indexing counts do not equal expected_ticks")
        expected = self.embedded_ticks / self.expected_ticks if self.expected_ticks else 0.0
        if abs(self.scored_coverage - expected) > 1e-9:
            raise ValueError("scored_coverage must be embedded_ticks / expected_ticks")
        return self


class CandidateGenerationSummary(ContractModel):
    channels_run: list[str] = Field(default_factory=list)
    qualifying_windows: int = Field(ge=0)
    candidate_recall_operating_point: str = "not_yet_measured"
    exact_scoring_completed: bool


class GraphExecutionSummary(ContractModel):
    enabled: bool = False
    pattern_id: str | None = None
    tracklets_considered: int = Field(default=0, ge=0)
    joins_completed: int = Field(default=0, ge=0)
    join_budget_reached: bool = False
    observation_scope_assessable: bool | None = None


class AssemblySummary(ContractModel):
    anchor_candidates_qualifying: int = Field(default=0, ge=0)
    anchor_candidates_retained: int = Field(default=0, ge=0)
    episodes_generated: int = Field(default=0, ge=0)
    assembly_complete: bool = True


class VerificationSummary(ContractModel):
    state: str = Field(pattern=r"^(complete|budget_reached|system_failure)$")
    clusters_total: int = Field(ge=0)
    clusters_verified: int = Field(ge=0)
    seconds_used: float = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "VerificationSummary":
        if self.clusters_verified > self.clusters_total:
            raise ValueError("clusters_verified cannot exceed clusters_total")
        if self.state == "complete" and self.clusters_verified != self.clusters_total:
            raise ValueError("complete verification must cover every cluster")
        return self


class CandidateOutcome(ContractModel):
    candidate_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    camera_id: str | None = None
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    constraints: list[ConstraintEvidence] = Field(default_factory=list)
    rationale: str | None = None
    retrieval_lanes: list[str] = Field(default_factory=list)
    graph_node_ids: list[str] = Field(default_factory=list)
    graph_edge_ids: list[str] = Field(default_factory=list)
    preview_url: str | None = None
    verification_cached: bool = False


class SearchResult(ContractModel):
    search_id: str = Field(min_length=1)
    interpretation: QueryPlan
    scope: dict[str, Any]
    indexing: IndexingSummary
    candidate_generation: CandidateGenerationSummary
    graph_execution: GraphExecutionSummary = Field(default_factory=GraphExecutionSummary)
    assembly: AssemblySummary = Field(default_factory=AssemblySummary)
    verification: VerificationSummary
    verified_matches: list[CandidateOutcome] = Field(default_factory=list)
    unresolved_visual: list[CandidateOutcome] = Field(default_factory=list)
    unresolved_system: list[CandidateOutcome] = Field(default_factory=list)
    rejected_near_misses: list[CandidateOutcome] = Field(default_factory=list)
    archive_conclusion: ArchiveConclusion

    @model_validator(mode="after")
    def enforce_honest_conclusion(self) -> "SearchResult":
        incomplete = (
            self.verification.state == "budget_reached"
            or not self.assembly.assembly_complete
            or self.graph_execution.join_budget_reached
        )
        if incomplete and self.archive_conclusion == ArchiveConclusion.NO_VERIFIED_MATCH:
            raise ValueError("an incomplete search cannot be a clean no-match")
        if self.verified_matches and self.archive_conclusion != ArchiveConclusion.VERIFIED_MATCHES_FOUND:
            raise ValueError("verified matches require verified_matches_found conclusion")
        if (
            not self.verified_matches
            and self.unresolved_visual
            and self.archive_conclusion == ArchiveConclusion.NO_VERIFIED_MATCH
        ):
            raise ValueError("unresolved visual evidence cannot be a clean no-match")
        return self

    def headline(self) -> str:
        if self.interpretation.state == "clarification_required":
            return "CLARIFICATION REQUIRED"
        if self.verified_matches:
            count = len(self.verified_matches)
            base = (
                "1 VERIFIED MATCH FOUND"
                if count == 1
                else f"{count} VERIFIED MATCHES FOUND"
            )
        elif self.unresolved_visual:
            base = "INSUFFICIENT VISUAL EVIDENCE"
        elif self.archive_conclusion == ArchiveConclusion.NO_VERIFIED_MATCH:
            base = "NO VERIFIED MATCH AT CURRENT OPERATING POINT"
        elif self.archive_conclusion == ArchiveConclusion.SEARCH_INCOMPLETE:
            base = "SEARCH INCOMPLETE"
        else:
            base = "NO RESULT"
        if (
            base != "SEARCH INCOMPLETE"
            and (
                self.verification.state == "budget_reached"
                or not self.assembly.assembly_complete
                or self.graph_execution.join_budget_reached
            )
        ):
            base += " — SEARCH INCOMPLETE"
        if self.unresolved_system:
            base += f" — SYSTEM COULD NOT ASSESS {len(self.unresolved_system)} CANDIDATES"
        return base


class ExportManifest(ContractModel):
    export_id: str = Field(min_length=1)
    search_id: str | None = None
    source_file_id: str = Field(min_length=1)
    camera_id: str | None = None
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_start_pts: float = Field(ge=0)
    requested_end_pts: float = Field(ge=0)
    actual_start_pts: float = Field(ge=0)
    actual_end_pts: float = Field(ge=0)
    context_padding_s: float = Field(default=0, ge=0)
    extraction_mode: str = Field(pattern=r"^(preview|evidence)$")
    ffmpeg_command: list[str] = Field(min_length=1)
    ffmpeg_version: str = Field(min_length=1)
    source_timebase: str = Field(min_length=1)
    operating_point_config_hash: str = Field(min_length=1)
    pipeline_git_commit: str = Field(min_length=1)
    model_revisions: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    output_clip_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    def canonical_payload(self) -> bytes:
        payload = self.model_dump(mode="json", by_alias=True, exclude={"manifest_sha256"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    def computed_manifest_sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload()).hexdigest()

    def with_computed_hash(self) -> "ExportManifest":
        return self.model_copy(update={"manifest_sha256": self.computed_manifest_sha256()})

    def verify_manifest_hash(self) -> bool:
        return bool(self.manifest_sha256) and self.manifest_sha256 == self.computed_manifest_sha256()
