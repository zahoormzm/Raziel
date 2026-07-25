"""Validated overlay records; rendering is optional and verdict-independent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizedBox:
    frame_id: int
    phrase: str
    x0: float
    y0: float
    x1: float
    y1: float
    source: str

    def __post_init__(self) -> None:
        values = (self.x0, self.y0, self.x1, self.y1)
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("box coordinates must be normalized")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("overlay box has no area")
        if not self.phrase.strip():
            raise ValueError("overlay phrase is required")
