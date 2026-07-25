"""Candidate-only grounding with an all-or-honest-fallback overlay policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from packages.contracts.query_plan import AtomType, QueryPlan
from packages.contracts.verification import EvidenceState, VerificationResult
from query.decide import CandidateDecision
from query.verify import EvidenceFrame


@dataclass(frozen=True)
class GroundingGateReport:
    status: str
    passed: bool
    blockers: tuple[str, ...]
    evaluated_instances: int


def evaluate_grounding_gate(
    *,
    evaluated_instances: int | None,
    correct_correspondence_instances: int | None,
    grossly_incorrect_boxes: int | None,
    overlay_latency_s: float | None,
    max_overlay_latency_s: float | None,
    core_verdict_unchanged_when_disabled: bool | None,
) -> GroundingGateReport:
    values = (
        evaluated_instances,
        correct_correspondence_instances,
        grossly_incorrect_boxes,
        overlay_latency_s,
        max_overlay_latency_s,
        core_verdict_unchanged_when_disabled,
    )
    if any(value is None for value in values):
        return GroundingGateReport(
            status="not_measured",
            passed=False,
            blockers=("grounding gate measurements are incomplete",),
            evaluated_instances=int(evaluated_instances or 0),
        )
    assert evaluated_instances is not None
    assert correct_correspondence_instances is not None
    assert grossly_incorrect_boxes is not None
    assert overlay_latency_s is not None
    assert max_overlay_latency_s is not None
    blockers: list[str] = []
    if evaluated_instances < 20:
        blockers.append("fewer than 20 supported object/attribute evidence instances")
    if correct_correspondence_instances != evaluated_instances:
        blockers.append("not every evaluated grounded region corresponds to its cited object")
    if grossly_incorrect_boxes:
        blockers.append("grossly incorrect boxes were observed")
    if overlay_latency_s > max_overlay_latency_s:
        blockers.append("overlay latency exceeds the declared demo budget")
    if not core_verdict_unchanged_when_disabled:
        blockers.append("turning grounding off changes the core verdict")
    return GroundingGateReport(
        status="passed" if not blockers else "failed",
        passed=not blockers,
        blockers=tuple(blockers),
        evaluated_instances=evaluated_instances,
    )


@dataclass(frozen=True)
class GroundingBox:
    frame_id: int
    constraint_id: str
    phrase: str
    x0: float
    y0: float
    x1: float
    y1: float
    detector_score: float
    detector_revision: str

    def validate(self, supplied_frame_ids: set[int]) -> None:
        if self.frame_id not in supplied_frame_ids:
            raise ValueError("grounding cited a frame outside the verified evidence")
        if not (0 <= self.x0 < self.x1 <= 1 and 0 <= self.y0 < self.y1 <= 1):
            raise ValueError("grounding boxes must use normalized [0,1] coordinates")
        if not 0 <= self.detector_score <= 1:
            raise ValueError("detector score must be in [0,1]")
        if not self.phrase or not self.detector_revision:
            raise ValueError("grounding phrase and detector revision are required")


class GroundingBackend(Protocol):
    @property
    def detector_revision(self) -> str: ...

    def ground(
        self,
        frame: EvidenceFrame,
        *,
        constraint_id: str,
        phrase: str,
    ) -> Sequence[GroundingBox]: ...


@dataclass(frozen=True)
class SpatialEvidence:
    candidate_id: str
    overlay_enabled: bool
    boxes: tuple[GroundingBox, ...]
    cited_frame_ids: tuple[int, ...]
    fallback_reason: str | None
    gate_status: str


def ground_verified_candidate(
    decision: CandidateDecision,
    verification: VerificationResult,
    query_plan: QueryPlan,
    frames: Sequence[EvidenceFrame],
    *,
    backend: GroundingBackend | None,
    feature_enabled: bool,
    gate: GroundingGateReport | None,
) -> SpatialEvidence:
    cited = tuple(
        sorted(
            {
                frame_id
                for item in [*verification.atoms, *verification.relations, *verification.logic_groups]
                for frame_id in item.evidence_frame_ids
            }
        )
    )
    if not decision.verified_match:
        raise ValueError("spatial grounding is legal only for verified candidates")
    if not feature_enabled or backend is None:
        return SpatialEvidence(
            candidate_id=decision.candidate_id,
            overlay_enabled=False,
            boxes=(),
            cited_frame_ids=cited,
            fallback_reason="grounding_disabled",
            gate_status="disabled",
        )
    if gate is None or not gate.passed:
        return SpatialEvidence(
            candidate_id=decision.candidate_id,
            overlay_enabled=False,
            boxes=(),
            cited_frame_ids=cited,
            fallback_reason="grounding_gate_not_passed",
            gate_status=gate.status if gate else "not_measured",
        )

    frame_by_id = {frame.frame_id: frame for frame in frames}
    supported = {
        item.constraint_id: item
        for item in verification.atoms
        if item.state == EvidenceState.SUPPORTED
    }
    phrases = {
        atom.atom_id: atom.text_span
        for atom in query_plan.atoms
        if atom.type in {AtomType.OBJECT, AtomType.ATTRIBUTE} and atom.atom_id in supported
    }
    if not phrases:
        return SpatialEvidence(
            candidate_id=decision.candidate_id,
            overlay_enabled=False,
            boxes=(),
            cited_frame_ids=cited,
            fallback_reason="no_supported_object_or_attribute_phrase",
            gate_status=gate.status,
        )

    boxes: list[GroundingBox] = []
    try:
        for constraint_id, phrase in phrases.items():
            evidence_ids = supported[constraint_id].evidence_frame_ids
            if not evidence_ids:
                raise ValueError("supported grounded constraint has no cited evidence")
            constraint_boxes: list[GroundingBox] = []
            for frame_id in evidence_ids:
                frame = frame_by_id.get(frame_id)
                if frame is None:
                    raise ValueError("grounding evidence frame is not supplied")
                predicted = list(
                    backend.ground(
                        frame,
                        constraint_id=constraint_id,
                        phrase=phrase,
                    )
                )
                for box in predicted:
                    box.validate(set(frame_by_id))
                    if box.constraint_id != constraint_id or box.phrase != phrase:
                        raise ValueError("grounding output changed the requested reference")
                constraint_boxes.extend(predicted)
            if not constraint_boxes:
                raise ValueError(f"no stable grounded region for {constraint_id}")
            boxes.extend(constraint_boxes)
    except Exception as exc:
        # Partial or unstable overlays are intentionally discarded.
        return SpatialEvidence(
            candidate_id=decision.candidate_id,
            overlay_enabled=False,
            boxes=(),
            cited_frame_ids=cited,
            fallback_reason=f"{type(exc).__name__}: {exc}",
            gate_status=gate.status,
        )
    return SpatialEvidence(
        candidate_id=decision.candidate_id,
        overlay_enabled=True,
        boxes=tuple(boxes),
        cited_frame_ids=cited,
        fallback_reason=None,
        gate_status=gate.status,
    )


class LocalGroundingDinoBackend:
    """Lazy local-files-only Grounding DINO adapter."""

    def __init__(
        self,
        model_path: str,
        *,
        detector_revision: str,
        box_threshold: float,
        text_threshold: float,
    ) -> None:
        try:
            import torch
            from PIL import Image
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        except ImportError as exc:  # pragma: no cover - dependency-dependent path
            raise RuntimeError("Grounding DINO dependencies are unavailable") from exc
        self._torch = torch
        self._image = Image
        self._processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            model_path, local_files_only=True
        )
        self._model.eval()
        self._revision = detector_revision
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold

    @property
    def detector_revision(self) -> str:
        return self._revision

    def ground(
        self,
        frame: EvidenceFrame,
        *,
        constraint_id: str,
        phrase: str,
    ) -> Sequence[GroundingBox]:
        if not frame.asset_path:
            raise ValueError("local grounding requires an evidence-frame asset path")
        image = self._image.open(frame.asset_path).convert("RGB")
        inputs = self._processor(images=image, text=phrase, return_tensors="pt")
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        processed = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            box_threshold=self.box_threshold,
            text_threshold=self.text_threshold,
            target_sizes=[image.size[::-1]],
        )[0]
        width, height = image.size
        result: list[GroundingBox] = []
        for raw_box, raw_score in zip(processed["boxes"], processed["scores"]):
            x0, y0, x1, y1 = (float(value) for value in raw_box.tolist())
            result.append(
                GroundingBox(
                    frame_id=frame.frame_id,
                    constraint_id=constraint_id,
                    phrase=phrase,
                    x0=x0 / width,
                    y0=y0 / height,
                    x1=x1 / width,
                    y1=y1 / height,
                    detector_score=float(raw_score),
                    detector_revision=self.detector_revision,
                )
            )
        return result
