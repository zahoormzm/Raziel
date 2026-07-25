"""Optional propagation for a short, accepted interval only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from grounding.boxes import GroundingBox, GroundingGateReport
from query.decide import CandidateDecision
from query.verify import EvidenceFrame


class BoxPropagationBackend(Protocol):
    @property
    def model_revision(self) -> str: ...

    def propagate(
        self,
        seed_boxes: Sequence[GroundingBox],
        frames: Sequence[EvidenceFrame],
    ) -> Sequence[GroundingBox]: ...


@dataclass(frozen=True)
class PropagationResult:
    enabled: bool
    boxes: tuple[GroundingBox, ...]
    fallback_reason: str | None


def propagate_verified_interval(
    decision: CandidateDecision,
    *,
    seed_boxes: Sequence[GroundingBox],
    accepted_interval_frames: Sequence[EvidenceFrame],
    backend: BoxPropagationBackend | None,
    feature_enabled: bool,
    grounding_gate: GroundingGateReport | None,
) -> PropagationResult:
    if not decision.verified_match:
        raise ValueError("box/mask propagation is legal only for an accepted candidate")
    if not feature_enabled or backend is None:
        return PropagationResult(False, tuple(seed_boxes), "propagation_disabled")
    if grounding_gate is None or not grounding_gate.passed:
        return PropagationResult(False, tuple(seed_boxes), "grounding_gate_not_passed")
    if not seed_boxes:
        return PropagationResult(False, (), "no_valid_seed_boxes")
    supplied = {frame.frame_id for frame in accepted_interval_frames}
    try:
        propagated = tuple(backend.propagate(seed_boxes, accepted_interval_frames))
        if not propagated:
            raise ValueError("propagation returned no stable overlay")
        for box in propagated:
            box.validate(supplied)
    except Exception as exc:
        return PropagationResult(False, tuple(seed_boxes), f"{type(exc).__name__}: {exc}")
    return PropagationResult(True, propagated, None)


class LocalSam2PropagationBackend:
    """Lazy adapter around a caller-supplied local SAM 2 predictor.

    SAM 2 APIs vary by pinned environment, so construction takes an already
    configured predictor factory and never downloads a checkpoint.
    """

    def __init__(self, predictor_factory: object, *, model_revision: str) -> None:
        if not callable(predictor_factory):
            raise TypeError("predictor_factory must be callable")
        self._factory = predictor_factory
        self._revision = model_revision

    @property
    def model_revision(self) -> str:
        return self._revision

    def propagate(
        self,
        seed_boxes: Sequence[GroundingBox],
        frames: Sequence[EvidenceFrame],
    ) -> Sequence[GroundingBox]:
        predictor = self._factory()
        return predictor.propagate(seed_boxes=seed_boxes, frames=frames)
