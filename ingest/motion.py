"""Low-resolution motion/activity scoring and tick densification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .quality import _gray_pixels


@dataclass(frozen=True)
class MotionSample:
    pts_seconds: float
    score: float


def frame_difference(previous: Any, current: Any) -> float:
    left, left_width, left_height = _gray_pixels(previous)
    right, right_width, right_height = _gray_pixels(current)
    if (left_width, left_height) != (right_width, right_height):
        raise ValueError("motion frames must have equal dimensions")
    if not left:
        return 0.0
    return sum(abs(a - b) for a, b in zip(left, right)) / (255.0 * len(left))


def motion_ticks(
    samples: Iterable[MotionSample],
    *,
    threshold: float,
    dense_fps: float = 4.0,
    padding_s: float = 1.0,
    duration_s: float | None = None,
) -> list[float]:
    """Create deterministic dense ticks around above-threshold samples."""
    if dense_fps <= 0:
        raise ValueError("dense_fps must be positive")
    intervals: list[tuple[float, float]] = []
    for sample in samples:
        if sample.score >= threshold:
            intervals.append(
                (
                    max(0.0, sample.pts_seconds - padding_s),
                    sample.pts_seconds + padding_s,
                )
            )
    if not intervals:
        return []
    intervals.sort()
    merged: list[list[float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    step_us = max(1, round(1_000_000 / dense_fps))
    ticks: set[int] = set()
    for start, end in merged:
        start_us = round(start * 1_000_000)
        end_us = round(end * 1_000_000)
        if duration_s is not None:
            end_us = min(end_us, max(0, round(duration_s * 1_000_000) - 1))
        value = (start_us // step_us) * step_us
        if value < start_us:
            value += step_us
        while value <= end_us:
            ticks.add(value)
            value += step_us
    return [value / 1_000_000 for value in sorted(ticks)]
