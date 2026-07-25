"""Deterministic frame-ID-to-PTS boundary refinement."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BoundaryFrame:
    frame_id: int
    pts: float
    relevance: float | None = None


@dataclass(frozen=True)
class BoundaryConfig:
    relevance_threshold: float
    pre_padding_s: float
    post_padding_s: float

    def validate(self) -> None:
        if not 0.0 <= self.relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be in [0, 1]")
        if self.pre_padding_s < 0 or self.post_padding_s < 0:
            raise ValueError("boundary padding must be nonnegative")


@dataclass(frozen=True)
class RefinedBoundary:
    onset_frame_id: int
    offset_frame_id: int
    onset_pts: float
    offset_pts: float
    padded_start_pts: float
    padded_end_pts: float
    pre_padding_s: float
    post_padding_s: float
    method: str


@dataclass(frozen=True)
class BoundaryErrorReport:
    sample_count: int
    median_start_absolute_error_s: float | None
    median_end_absolute_error_s: float | None
    median_combined_error_s: float | None


def refine_boundaries(
    dense_frames: Sequence[BoundaryFrame],
    *,
    proposed_start_frame_id: int,
    proposed_end_frame_id: int,
    config: BoundaryConfig,
    focused_start_frame_id: int | None = None,
    focused_end_frame_id: int | None = None,
    source_duration_s: float | None = None,
) -> RefinedBoundary:
    config.validate()
    if not dense_frames:
        raise ValueError("dense refinement requires decoded frames")
    by_id = {frame.frame_id: frame for frame in dense_frames}
    if len(by_id) != len(dense_frames):
        raise ValueError("dense frame IDs must be unique")
    if tuple(frame.pts for frame in dense_frames) != tuple(
        sorted(frame.pts for frame in dense_frames)
    ):
        raise ValueError("dense frames must be ordered by source PTS")
    for frame_id in (proposed_start_frame_id, proposed_end_frame_id):
        if frame_id not in by_id:
            raise ValueError("proposed boundary frame is absent from dense evidence")

    if (focused_start_frame_id is None) != (focused_end_frame_id is None):
        raise ValueError("focused start/end IDs must be supplied together")
    if focused_start_frame_id is not None and focused_end_frame_id is not None:
        if focused_start_frame_id not in by_id or focused_end_frame_id not in by_id:
            raise ValueError("focused verifier cited a frame outside dense evidence")
        onset = by_id[focused_start_frame_id]
        offset = by_id[focused_end_frame_id]
        method = "focused_verifier"
    else:
        relevant = [
            frame
            for frame in dense_frames
            if frame.relevance is not None
            and frame.relevance >= config.relevance_threshold
        ]
        if relevant:
            onset, offset = relevant[0], relevant[-1]
            method = "frame_relevance"
        else:
            onset = by_id[proposed_start_frame_id]
            offset = by_id[proposed_end_frame_id]
            method = "proposed_evidence_fallback"

    if offset.pts < onset.pts:
        raise ValueError("refined event offset precedes onset")
    padded_start = max(0.0, onset.pts - config.pre_padding_s)
    padded_end = offset.pts + config.post_padding_s
    if source_duration_s is not None:
        if source_duration_s < 0:
            raise ValueError("source duration must be nonnegative")
        padded_end = min(source_duration_s, padded_end)
    return RefinedBoundary(
        onset_frame_id=onset.frame_id,
        offset_frame_id=offset.frame_id,
        onset_pts=onset.pts,
        offset_pts=offset.pts,
        padded_start_pts=padded_start,
        padded_end_pts=padded_end,
        pre_padding_s=config.pre_padding_s,
        post_padding_s=config.post_padding_s,
        method=method,
    )


def measure_boundary_error(
    predictions: Sequence[RefinedBoundary],
    ground_truth: Sequence[tuple[float, float]],
) -> BoundaryErrorReport:
    if len(predictions) != len(ground_truth):
        raise ValueError("prediction and ground-truth lengths differ")
    if not predictions:
        return BoundaryErrorReport(0, None, None, None)
    start_errors = [
        abs(prediction.onset_pts - truth[0])
        for prediction, truth in zip(predictions, ground_truth)
    ]
    end_errors = [
        abs(prediction.offset_pts - truth[1])
        for prediction, truth in zip(predictions, ground_truth)
    ]
    combined = [
        (start_error + end_error) / 2
        for start_error, end_error in zip(start_errors, end_errors)
    ]
    return BoundaryErrorReport(
        sample_count=len(predictions),
        median_start_absolute_error_s=median(start_errors),
        median_end_absolute_error_s=median(end_errors),
        median_combined_error_s=median(combined),
    )


def map_frame_ids_to_pts(
    frame_ids: Sequence[int], frames: Sequence[BoundaryFrame]
) -> Mapping[int, float]:
    by_id = {frame.frame_id: frame.pts for frame in frames}
    missing = set(frame_ids).difference(by_id)
    if missing:
        raise ValueError(f"unknown frame IDs: {sorted(missing)}")
    return {frame_id: by_id[frame_id] for frame_id in frame_ids}
