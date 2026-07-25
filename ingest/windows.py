"""Multi-scale temporal window generation and aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


DEFAULT_WINDOW_SCALES = ((4, 2), (12, 6), (30, 15))


@dataclass(frozen=True)
class Window:
    scale_s: int
    t0: float
    t1: float


def build_windows(
    duration_s: float,
    scales: Iterable[tuple[int, int]] = DEFAULT_WINDOW_SCALES,
) -> list[Window]:
    if duration_s < 0:
        raise ValueError("duration_s cannot be negative")
    windows: list[Window] = []
    for scale_s, stride_s in scales:
        if scale_s <= 0 or stride_s <= 0:
            raise ValueError("window scales and strides must be positive")
        start = 0.0
        if duration_s == 0:
            continue
        while start < duration_s:
            end = min(duration_s, start + scale_s)
            windows.append(Window(scale_s=scale_s, t0=start, t1=end))
            if end >= duration_s:
                break
            start += stride_s
    return windows


def aggregate_window_scores(
    tick_pts: Sequence[float],
    scores: Sequence[float],
    windows: Iterable[Window],
    *,
    reduction: str = "max",
) -> list[float | None]:
    if len(tick_pts) != len(scores):
        raise ValueError("tick_pts and scores must have equal length")
    output: list[float | None] = []
    for window in windows:
        values = [
            float(score)
            for pts, score in zip(tick_pts, scores)
            if window.t0 <= pts < window.t1
        ]
        if not values:
            output.append(None)
        elif reduction == "max":
            output.append(max(values))
        elif reduction == "mean":
            output.append(sum(values) / len(values))
        else:
            raise ValueError(f"unsupported reduction: {reduction}")
    return output
