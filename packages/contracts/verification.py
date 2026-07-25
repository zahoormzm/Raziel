"""Structured verifier boundary contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .base import ContractModel


class EvidenceState(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNOBSERVABLE = "unobservable"
    UNDETERMINED = "undetermined"


class ConstraintEvidence(ContractModel):
    constraint_id: str = Field(min_length=1)
    state: EvidenceState
    reason_code: str = Field(min_length=1)
    evidence_frame_ids: list[int] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_evidence(self) -> "ConstraintEvidence":
        if self.state in (EvidenceState.SUPPORTED, EvidenceState.CONTRADICTED):
            if not self.evidence_frame_ids:
                raise ValueError("supported/contradicted constraints require cited evidence")
        return self


class MatchingSubinterval(ContractModel):
    start_frame_id: int = Field(ge=0)
    end_frame_id: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "MatchingSubinterval":
        if self.end_frame_id < self.start_frame_id:
            raise ValueError("end_frame_id cannot precede start_frame_id")
        return self


class VerificationResult(ContractModel):
    candidate_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    prompt_schema_version: str = Field(min_length=1)
    atoms: list[ConstraintEvidence] = Field(default_factory=list)
    relations: list[ConstraintEvidence] = Field(default_factory=list)
    logic_groups: list[ConstraintEvidence] = Field(default_factory=list)
    matching_subintervals: list[MatchingSubinterval] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0, le=1)
    cached: bool = False

    def validate_citations(self, supplied_frame_ids: set[int]) -> None:
        cited = {
            frame_id
            for item in [*self.atoms, *self.relations, *self.logic_groups]
            for frame_id in item.evidence_frame_ids
        }
        unknown = cited.difference(supplied_frame_ids)
        if unknown:
            raise ValueError(f"verifier cited unknown frame IDs: {sorted(unknown)}")
