"""Gated native-video clip embedding abstractions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .exact_score import ExactScoreResult, NumpyExactScorer, normalize
from .stores import VersionedEmbeddingStore


@dataclass(frozen=True)
class ClipSpan:
    video_id: str
    t0: float
    t1: float


class ClipEncoder(Protocol):
    @property
    def dimension(self) -> int: ...

    @property
    def revision(self) -> str: ...

    def encode(self, clips: Sequence[Any]) -> Sequence[Sequence[float]]: ...


class ClipEmbeddingIndex:
    """Exact clip scoring in a declared video/time scope."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        store: VersionedEmbeddingStore,
        *,
        block_rows: int = 65536,
    ):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.store = store
        self.scorer = NumpyExactScorer(store, block_rows=block_rows)

    def scoped_spans(
        self,
        *,
        video_ids: Sequence[str] = (),
        t0: float | None = None,
        t1: float | None = None,
    ) -> list[tuple[int, ClipSpan, int]]:
        clauses = ["clip_embedding_row IS NOT NULL"]
        params: list[Any] = []
        if video_ids:
            clauses.append(
                "video_id IN (" + ",".join("?" for _ in video_ids) + ")"
            )
            params.extend(video_ids)
        if t0 is not None:
            clauses.append("t1>?")
            params.append(t0)
        if t1 is not None:
            clauses.append("t0<?")
            params.append(t1)
        rows = self.connection.execute(
            f"""
            SELECT clip_id,video_id,t0,t1,clip_embedding_row
            FROM clips WHERE {' AND '.join(clauses)}
            ORDER BY video_id,t0,clip_id
            """,
            params,
        )
        return [
            (
                int(row["clip_id"]),
                ClipSpan(row["video_id"], row["t0"], row["t1"]),
                int(row["clip_embedding_row"]),
            )
            for row in rows
        ]

    def exact_score(
        self, query: Sequence[float], **scope: Any
    ) -> tuple[list[tuple[int, ClipSpan, int]], ExactScoreResult]:
        spans = self.scoped_spans(**scope)
        result = self.scorer.score(query, rows=[item[2] for item in spans])
        if len(result.scores) != len(spans):
            raise AssertionError("every embedded clip in scope must receive one score")
        return spans, result


def overlapping_clips(
    video_id: str,
    duration_s: float,
    *,
    clip_s: float = 12.0,
    stride_s: float = 6.0,
) -> list[ClipSpan]:
    if clip_s <= 0 or stride_s <= 0:
        raise ValueError("clip and stride must be positive")
    spans = []
    start = 0.0
    while start < duration_s:
        spans.append(ClipSpan(video_id, start, min(duration_s, start + clip_s)))
        if start + clip_s >= duration_s:
            break
        start += stride_s
    return spans


def index_clip_batch(
    *,
    connection: sqlite3.Connection,
    store: VersionedEmbeddingStore,
    encoder: ClipEncoder,
    spans: Sequence[ClipSpan],
    clips: Sequence[Any],
    enabled: bool,
) -> range:
    if not enabled:
        raise RuntimeError("clip_lane capability is disabled")
    if len(spans) != len(clips):
        raise ValueError("spans and clips must have equal length")
    if encoder.dimension != store.dimension:
        raise ValueError("encoder/store dimension mismatch")
    embeddings = [normalize(row) for row in encoder.encode(clips)]
    start = store.row_count
    rows = store.append(embeddings)
    try:
        with connection:
            for span, row in zip(spans, rows):
                connection.execute(
                    """
                    INSERT INTO clips(
                        video_id,t0,t1,clip_embedding_row,embedding_model_version
                    ) VALUES (?,?,?,?,?)
                    """,
                    (span.video_id, span.t0, span.t1, row, encoder.revision),
                )
    except Exception:
        store.truncate(start)
        raise
    return rows
