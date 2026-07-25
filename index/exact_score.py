"""Exact complete matrix scoring in bounded memory."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from .stores import VersionedEmbeddingStore


@dataclass(frozen=True)
class ExactScoreResult:
    row_ids: tuple[int, ...]
    scores: tuple[float, ...]
    exact_scoring_completed: bool = True

    def __post_init__(self) -> None:
        if len(self.row_ids) != len(self.scores):
            raise ValueError("every selected embedded row must receive exactly one score")


class ExactScorer(Protocol):
    def score(
        self, query: Sequence[float], rows: Iterable[int] | None = None
    ) -> ExactScoreResult: ...


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if norm == 0:
        raise ValueError("cannot normalize a zero vector")
    return [float(value) / norm for value in vector]


class NumpyExactScorer:
    def __init__(self, store: VersionedEmbeddingStore, *, block_rows: int = 65536):
        if block_rows <= 0:
            raise ValueError("block_rows must be positive")
        self.store = store
        self.block_rows = block_rows

    def score(
        self, query: Sequence[float], rows: Iterable[int] | None = None
    ) -> ExactScoreResult:
        if len(query) != self.store.dimension:
            raise ValueError("query dimension does not match the embedding store")
        normalized = normalize(query)
        selected = (
            list(range(self.store.row_count))
            if rows is None
            else [int(row) for row in rows]
        )
        if any(row < 0 or row >= self.store.row_count for row in selected):
            raise IndexError("scope contains an invalid embedding row")
        if not selected:
            return ExactScoreResult((), ())
        try:
            import numpy as np  # type: ignore
        except ImportError:
            matrix_rows = self.store.read_rows(selected)
            scores = [
                sum(left * right for left, right in zip(row, normalized))
                for row in matrix_rows
            ]
            return ExactScoreResult(tuple(selected), tuple(scores))

        query_array = np.asarray(normalized, dtype=np.float32)
        matrix = np.memmap(
            self.store.matrix_path,
            mode="r",
            dtype="<f4",
            shape=(self.store.row_count, self.store.dimension),
        )
        scores: list[float] = []
        for offset in range(0, len(selected), self.block_rows):
            block_ids = selected[offset : offset + self.block_rows]
            block = matrix[block_ids]
            block_scores = block @ query_array
            scores.extend(float(value) for value in block_scores)
        if len(scores) != len(selected):
            raise AssertionError("exact scorer failed to score every embedded row")
        return ExactScoreResult(tuple(selected), tuple(scores))
