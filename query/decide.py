"""Four-state candidate decisions and near-miss ordering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from packages.contracts.search_result import ArchiveConclusion
from packages.contracts.verification import (
    ConstraintEvidence,
    EvidenceState,
    VerificationResult,
)


@dataclass(frozen=True)
class CandidateDecision:
    candidate_id: str
    verified_match: bool
    rejected_near_match: bool
    unresolved_visual: bool
    unresolved_system: bool
    supported_required_ids: tuple[str, ...]
    contradicted_required_ids: tuple[str, ...]
    unobservable_required_ids: tuple[str, ...]
    undetermined_required_ids: tuple[str, ...]

    @property
    def all_required_resolved(self) -> bool:
        return not self.unresolved_visual and not self.unresolved_system


@dataclass(frozen=True)
class RankedNearMiss:
    candidate_id: str
    supported_required_count: int
    contradicted_required_count: int
    retrieval_priority: float
    contradiction_summary: tuple[str, ...]


def decide_candidate(
    verification: VerificationResult,
    *,
    required_constraint_ids: set[str],
) -> CandidateDecision:
    if not required_constraint_ids:
        raise ValueError("at least one required constraint is needed for a candidate verdict")
    constraints = [
        *verification.atoms,
        *verification.relations,
        *verification.logic_groups,
    ]
    by_id = _index_constraints(constraints)
    missing = required_constraint_ids.difference(by_id)
    if missing:
        raise ValueError(f"verification omitted required constraints: {sorted(missing)}")
    selected = [by_id[constraint_id] for constraint_id in sorted(required_constraint_ids)]
    supported = tuple(
        item.constraint_id for item in selected if item.state == EvidenceState.SUPPORTED
    )
    contradicted = tuple(
        item.constraint_id for item in selected if item.state == EvidenceState.CONTRADICTED
    )
    unobservable = tuple(
        item.constraint_id for item in selected if item.state == EvidenceState.UNOBSERVABLE
    )
    undetermined = tuple(
        item.constraint_id for item in selected if item.state == EvidenceState.UNDETERMINED
    )
    has_contradiction = bool(contradicted)
    all_supported = len(supported) == len(required_constraint_ids)
    # Visual and system unresolved flags are intentionally independent.  A candidate
    # with both states belongs in both reporting dimensions until the shared contract
    # defines a single-bucket policy.
    return CandidateDecision(
        candidate_id=verification.candidate_id,
        verified_match=all_supported,
        rejected_near_match=has_contradiction,
        unresolved_visual=not has_contradiction and bool(unobservable),
        unresolved_system=not has_contradiction and bool(undetermined),
        supported_required_ids=supported,
        contradicted_required_ids=contradicted,
        unobservable_required_ids=unobservable,
        undetermined_required_ids=undetermined,
    )


def rank_near_misses(
    decisions: Iterable[CandidateDecision],
    *,
    verification_by_candidate: Mapping[str, VerificationResult],
    retrieval_priority: Mapping[str, float],
) -> tuple[RankedNearMiss, ...]:
    near_misses: list[RankedNearMiss] = []
    for decision in decisions:
        if not decision.rejected_near_match:
            continue
        verification = verification_by_candidate[decision.candidate_id]
        by_id = _index_constraints(
            [*verification.atoms, *verification.relations, *verification.logic_groups]
        )
        summaries = tuple(
            f"{constraint_id}: {by_id[constraint_id].rationale or by_id[constraint_id].reason_code}"
            for constraint_id in decision.contradicted_required_ids
        )
        near_misses.append(
            RankedNearMiss(
                candidate_id=decision.candidate_id,
                supported_required_count=len(decision.supported_required_ids),
                contradicted_required_count=len(decision.contradicted_required_ids),
                retrieval_priority=float(retrieval_priority.get(decision.candidate_id, 0.0)),
                contradiction_summary=summaries,
            )
        )
    return tuple(
        sorted(
            near_misses,
            key=lambda item: (
                -item.supported_required_count,
                item.contradicted_required_count,
                -item.retrieval_priority,
                item.candidate_id,
            ),
        )
    )


def derive_archive_conclusion(
    decisions: Sequence[CandidateDecision],
    *,
    assembly_complete: bool,
    verification_complete: bool,
    graph_join_budget_reached: bool,
) -> ArchiveConclusion:
    if any(decision.verified_match for decision in decisions):
        return ArchiveConclusion.VERIFIED_MATCHES_FOUND
    if not assembly_complete or not verification_complete or graph_join_budget_reached:
        return ArchiveConclusion.SEARCH_INCOMPLETE
    has_visual = any(decision.unresolved_visual for decision in decisions)
    has_system = any(decision.unresolved_system for decision in decisions)
    if has_visual and has_system:
        raise ValueError(
            "mixed unresolved visual/system evidence requires the shared contract policy"
        )
    if has_visual:
        return ArchiveConclusion.INSUFFICIENT_VISUAL_EVIDENCE
    if has_system:
        # The result model has no system-failure archive conclusion.  NOT_APPLICABLE
        # avoids fabricating a clean no-match while unresolved-system evidence exists.
        return ArchiveConclusion.NOT_APPLICABLE
    return ArchiveConclusion.NO_VERIFIED_MATCH


def _index_constraints(
    constraints: Sequence[ConstraintEvidence],
) -> Mapping[str, ConstraintEvidence]:
    indexed: dict[str, ConstraintEvidence] = {}
    for constraint in constraints:
        if constraint.constraint_id in indexed:
            raise ValueError(f"duplicate constraint ID: {constraint.constraint_id}")
        indexed[constraint.constraint_id] = constraint
    return indexed
