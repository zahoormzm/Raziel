"""Dependency-light frame quality metrics."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _gray_array(frame: Any):
    """Return a 2-D float grayscale array, or None when NumPy is unavailable."""
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    array = np.asarray(frame)
    if array.ndim == 3:
        if array.shape[2] < 3:
            array = array[..., 0]
        else:
            array = (
                0.2126 * array[..., 0]
                + 0.7152 * array[..., 1]
                + 0.0722 * array[..., 2]
            )
    if array.ndim != 2:
        raise ValueError("frame must be a two-dimensional grayscale or RGB array")
    return array.astype(float, copy=False)


def _gray_pixels(frame: Any) -> tuple[list[float], int, int]:
    """Return flattened grayscale pixels without importing NumPy eagerly."""
    array = _gray_array(frame)
    if array is not None:
        height, width = array.shape
        return array.ravel().tolist(), int(width), int(height)

    if not isinstance(frame, Sequence) or not frame:
        raise ValueError("frame must be a non-empty nested sequence")
    rows = list(frame)
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError("frame rows must be rectangular")
    pixels: list[float] = []
    for row in rows:
        for value in row:
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                channels = list(value)
                if len(channels) < 3:
                    pixels.append(float(channels[0]))
                else:
                    pixels.append(
                        0.2126 * float(channels[0])
                        + 0.7152 * float(channels[1])
                        + 0.0722 * float(channels[2])
                    )
            else:
                pixels.append(float(value))
    return pixels, width, len(rows)


def luminance(frame: Any) -> float:
    array = _gray_array(frame)
    if array is not None:
        return float(array.mean())
    pixels, _, _ = _gray_pixels(frame)
    return sum(pixels) / len(pixels)


def sharpness(frame: Any) -> float:
    """Variance of a four-neighbour Laplacian; higher means sharper.

    The vectorised path computes the identical quantity as the pure-Python
    fallback below. Converting with NumPy and then discarding the vectorisation
    via ``.tolist()`` cost 1.07 s per 1080p frame, and this runs on every decoded
    frame during ingest and again per evidence frame at query time.
    """
    array = _gray_array(frame)
    if array is not None:
        height, width = array.shape
        if width < 3 or height < 3:
            return 0.0
        interior = array[1:-1, 1:-1]
        laplacian = (
            array[:-2, 1:-1]
            + array[2:, 1:-1]
            + array[1:-1, :-2]
            + array[1:-1, 2:]
            - 4.0 * interior
        )
        return float(laplacian.var())

    pixels, width, height = _gray_pixels(frame)
    if width < 3 or height < 3:
        return 0.0
    values: list[float] = []
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            center = pixels[y * width + x]
            laplacian = (
                pixels[(y - 1) * width + x]
                + pixels[(y + 1) * width + x]
                + pixels[y * width + x - 1]
                + pixels[y * width + x + 1]
                - 4.0 * center
            )
            values.append(laplacian)
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def assess_quality(frame: Any) -> tuple[float, float]:
    return luminance(frame), sharpness(frame)
