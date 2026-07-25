"""Frame encoder abstraction and restart-safe metadata commit."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from ingest.sampler import ExpectedTickLedger

from .exact_score import normalize
from .exact_score import ExactScoreResult, NumpyExactScorer
from .stores import VersionedEmbeddingStore


class FrameEncoder(Protocol):
    @property
    def dimension(self) -> int: ...

    @property
    def revision(self) -> str: ...

    def encode(self, frames: Sequence[Any]) -> Sequence[Sequence[float]]: ...


class FrameTextEncoder(Protocol):
    @property
    def dimension(self) -> int: ...

    @property
    def revision(self) -> str: ...

    def encode_text(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True)
class EmbeddedFrameTick:
    video_id: str
    camera_id: str | None
    target_pts: float
    actual_pts: float
    sampling_lane: str
    frame_id: int
    embedding_row: int


class FrameEmbeddingIndex:
    """SQLite metadata plus an exact, versioned frame matrix."""

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

    def scoped_ticks(
        self,
        *,
        video_ids: Sequence[str] = (),
        camera_ids: Sequence[str] = (),
        t0: float | None = None,
        t1: float | None = None,
        lanes: Sequence[str] = ("base", "motion"),
    ) -> list[EmbeddedFrameTick]:
        clauses = ["t.status='embedded'", "f.frame_embedding_row IS NOT NULL"]
        params: list[Any] = []
        if video_ids:
            clauses.append(
                "t.video_id IN (" + ",".join("?" for _ in video_ids) + ")"
            )
            params.extend(video_ids)
        if camera_ids:
            clauses.append(
                "v.camera_id IN (" + ",".join("?" for _ in camera_ids) + ")"
            )
            params.extend(camera_ids)
        if t0 is not None:
            clauses.append("t.target_pts>=?")
            params.append(t0)
        if t1 is not None:
            clauses.append("t.target_pts<?")
            params.append(t1)
        if lanes:
            clauses.append(
                "t.sampling_lane IN (" + ",".join("?" for _ in lanes) + ")"
            )
            params.extend(lanes)
        rows = self.connection.execute(
            f"""
            SELECT t.video_id,v.camera_id,t.target_pts,f.pts_seconds,
                   t.sampling_lane,f.frame_id,f.frame_embedding_row
            FROM sample_ticks t
            JOIN frames f ON f.frame_id=t.frame_id
            JOIN videos v ON v.video_id=t.video_id
            WHERE {' AND '.join(clauses)}
            ORDER BY t.video_id,t.target_pts,t.sampling_lane
            """,
            params,
        )
        return [
            EmbeddedFrameTick(
                video_id=row["video_id"],
                camera_id=row["camera_id"],
                target_pts=row["target_pts"],
                actual_pts=row["pts_seconds"],
                sampling_lane=row["sampling_lane"],
                frame_id=row["frame_id"],
                embedding_row=row["frame_embedding_row"],
            )
            for row in rows
        ]

    def exact_score(
        self,
        query: Sequence[float],
        **scope: Any,
    ) -> tuple[list[EmbeddedFrameTick], ExactScoreResult]:
        ticks = self.scoped_ticks(**scope)
        result = self.scorer.score(
            query, rows=[tick.embedding_row for tick in ticks]
        )
        if len(result.scores) != len(ticks):
            raise AssertionError("every embedded tick in scope must receive one score")
        return ticks, result

    def exact_score_text(
        self,
        text: str,
        encoder: FrameTextEncoder,
        **scope: Any,
    ) -> tuple[list[EmbeddedFrameTick], ExactScoreResult]:
        if encoder.dimension != self.store.dimension:
            raise ValueError("text encoder/store dimension mismatch")
        vectors = list(encoder.encode_text([text]))
        if len(vectors) != 1:
            raise ValueError("text encoder must return one vector per requested text")
        return self.exact_score(vectors[0], **scope)


class SigLIP2FrameEncoder:
    """Lazy Hugging Face adapter; model packages are never imported at module load."""

    def __init__(
        self,
        model_name: str = "google/siglip2-base-patch16-224",
        *,
        revision: str,
        device: str = "cuda",
        local_files_only: bool = True,
    ):
        if not revision:
            raise ValueError("an exact model revision is required")
        try:
            import torch  # type: ignore
            from transformers import AutoModel, AutoProcessor  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "SigLIP2 support requires torch and transformers in the indexing environment"
            ) from exc
        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(
            model_name,
            revision=revision,
            local_files_only=local_files_only,
            use_fast=False,
        )
        self._model = (
            AutoModel.from_pretrained(
                model_name,
                revision=revision,
                local_files_only=local_files_only,
            )
            .to(device)
            .eval()
        )
        self._device = device
        self._revision = revision
        text_config = getattr(self._model.config, "text_config", None)
        self._text_max_length = int(
            getattr(text_config, "max_position_embeddings", 64)
        )
        dimension = (
            getattr(self._model.config, "projection_dim", None)
            or getattr(text_config, "projection_size", None)
            or getattr(text_config, "hidden_size", None)
        )
        if not dimension:
            raise RuntimeError("unable to determine SigLIP2 projection dimension")
        self._dimension = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def revision(self) -> str:
        return self._revision

    def encode(self, frames: Sequence[Any]) -> Sequence[Sequence[float]]:
        inputs = self._processor(images=list(frames), return_tensors="pt").to(self._device)
        with self._torch.inference_mode():
            features = self._model.get_image_features(**inputs)
        return features.detach().float().cpu().tolist()

    def encode_text(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        inputs = self._processor(
            text=list(texts),
            padding="max_length",
            truncation=True,
            max_length=self._text_max_length,
            return_tensors="pt",
        ).to(self._device)
        with self._torch.inference_mode():
            features = self._model.get_text_features(**inputs)
        return features.detach().float().cpu().tolist()


def index_frame_batch(
    *,
    connection: sqlite3.Connection,
    store: VersionedEmbeddingStore,
    encoder: FrameEncoder,
    frame_ids: Sequence[int],
    frames: Sequence[Any],
) -> range:
    """Durably append embeddings, then atomically link rows and tick states."""
    if len(frame_ids) != len(frames):
        raise ValueError("frame_ids and frames must have equal length")
    if encoder.dimension != store.dimension:
        raise ValueError("encoder/store dimension mismatch")
    embeddings = [normalize(row) for row in encoder.encode(frames)]
    start = store.row_count
    appended = store.append(embeddings)
    try:
        with connection:
            for frame_id, row in zip(frame_ids, appended):
                current = connection.execute(
                    "SELECT frame_embedding_row FROM frames WHERE frame_id=?",
                    (frame_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(f"unknown frame_id {frame_id}")
                if current[0] is not None and current[0] != row:
                    raise ValueError(f"frame {frame_id} already maps to another row")
                connection.execute(
                    "UPDATE frames SET frame_embedding_row=? WHERE frame_id=?",
                    (row, frame_id),
                )
                connection.execute(
                    """
                    UPDATE sample_ticks SET status='embedded',error_code=NULL
                    WHERE frame_id=?
                    """,
                    (frame_id,),
                )
    except Exception:
        store.truncate(start)
        raise
    return appended
