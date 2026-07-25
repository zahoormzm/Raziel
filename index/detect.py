"""Offline, resumable detection over adaptive sampling ticks."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, ContextManager, Protocol, Sequence

from .stores import ResumableBlockState
from .stores import content_key


@dataclass(frozen=True)
class Detection:
    frame_id: int
    label: str
    bbox: tuple[float, float, float, float]
    confidence: float | None

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = self.bbox
        if x1 < x0 or y1 < y0:
            raise ValueError("bbox must be ordered (x0,y0,x1,y1)")


class Detector(Protocol):
    @property
    def revision(self) -> str: ...

    def detect(self, frame_ids: Sequence[int], images: Sequence[Any]) -> Sequence[Detection]: ...


def detection_generation_key(
    *,
    source_sha256: str,
    sampling_policy_version: str,
    detector_revision: str,
    thresholds: dict[str, Any],
) -> str:
    return content_key(
        {
            "source_sha256": source_sha256,
            "sampling_policy_version": sampling_policy_version,
            "detector_revision": detector_revision,
            "thresholds": thresholds,
        }
    )


class GroundingDINODetector:
    """Lazy Transformers adapter for an explicitly versioned detector."""

    def __init__(
        self,
        labels: Sequence[str],
        *,
        model_name: str = "IDEA-Research/grounding-dino-tiny",
        revision: str,
        device: str = "cuda",
        box_threshold: float = 0.35,
        text_threshold: float = 0.25,
    ):
        if not labels:
            raise ValueError("at least one open-vocabulary label is required")
        if not revision:
            raise ValueError("an exact detector revision is required")
        try:
            import torch  # type: ignore
            from transformers import (  # type: ignore
                AutoModelForZeroShotObjectDetection,
                AutoProcessor,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Grounding DINO requires torch and transformers in the detector environment"
            ) from exc
        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(
            model_name, revision=revision
        )
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            model_name, revision=revision
        ).to(device)
        self._labels = list(labels)
        self._device = device
        self._revision = revision
        self._box_threshold = box_threshold
        self._text_threshold = text_threshold

    @property
    def revision(self) -> str:
        return self._revision

    def detect(
        self, frame_ids: Sequence[int], images: Sequence[Any]
    ) -> Sequence[Detection]:
        if len(frame_ids) != len(images):
            raise ValueError("frame_ids and images must have equal length")
        detections: list[Detection] = []
        for frame_id, image in zip(frame_ids, images):
            inputs = self._processor(
                images=image, text=[self._labels], return_tensors="pt"
            ).to(self._device)
            with self._torch.inference_mode():
                outputs = self._model(**inputs)
            height, width = image.shape[:2]
            processed = self._processor.post_process_grounded_object_detection(
                outputs,
                inputs.input_ids,
                threshold=self._box_threshold,
                text_threshold=self._text_threshold,
                target_sizes=[(height, width)],
            )[0]
            labels = processed.get("text_labels") or processed.get("labels")
            for box, confidence, label in zip(
                processed["boxes"], processed["scores"], labels
            ):
                detections.append(
                    Detection(
                        frame_id=int(frame_id),
                        label=str(label),
                        bbox=tuple(float(value) for value in box.tolist()),
                        confidence=float(confidence),
                    )
                )
        return detections


def adaptive_frame_ids(
    connection: sqlite3.Connection,
    video_id: str,
    *,
    include_motion: bool,
) -> list[int]:
    lanes = ("base", "motion") if include_motion else ("base",)
    placeholders = ",".join("?" for _ in lanes)
    rows = connection.execute(
        f"""
        SELECT DISTINCT frame_id FROM sample_ticks
        WHERE video_id=? AND frame_id IS NOT NULL
          AND sampling_lane IN ({placeholders})
        ORDER BY target_pts
        """,
        (video_id, *lanes),
    )
    return [int(row[0]) for row in rows]


def run_detection_block(
    *,
    connection: sqlite3.Connection,
    detector: Detector,
    frame_ids: Sequence[int],
    images: Sequence[Any],
    checkpoint: ResumableBlockState,
    block_id: str,
    enabled: bool,
    gpu_lease: ContextManager[Any] | None = None,
) -> int:
    if not enabled:
        raise RuntimeError("tracklet_lane capability is disabled")
    if checkpoint.is_complete(block_id):
        return 0
    if len(frame_ids) != len(images):
        raise ValueError("frame_ids and images must have equal length")
    allowed = set(frame_ids)
    with gpu_lease if gpu_lease is not None else nullcontext():
        detections = list(detector.detect(frame_ids, images))
    if any(item.frame_id not in allowed for item in detections):
        raise ValueError("detector returned a frame outside the requested block")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS detection_generation_items (
            generation_key TEXT NOT NULL,
            block_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            detection_id INTEGER NOT NULL,
            PRIMARY KEY(generation_key,block_id,ordinal),
            FOREIGN KEY(detection_id) REFERENCES detections(detection_id)
        )
        """
    )
    inserted = 0
    with connection:
        for ordinal, item in enumerate(detections):
            prior = connection.execute(
                """
                SELECT d.detection_id,d.frame_id,d.label,d.bbox_json,
                       d.confidence,d.detector_version
                FROM detection_generation_items i
                JOIN detections d ON d.detection_id=i.detection_id
                WHERE generation_key=? AND block_id=? AND ordinal=?
                """,
                (checkpoint.generation_key, block_id, ordinal),
            ).fetchone()
            if prior is not None:
                expected = (
                    item.frame_id,
                    item.label,
                    json.dumps(item.bbox, separators=(",", ":")),
                    item.confidence,
                    detector.revision,
                )
                actual = tuple(prior[index] for index in range(1, 6))
                if actual != expected:
                    raise ValueError(
                        "detector resume output differs within one artifact generation"
                    )
                continue
            cursor = connection.execute(
                """
                INSERT INTO detections(
                    frame_id,label,bbox_json,confidence,detector_version
                ) VALUES (?,?,?,?,?)
                """,
                (
                    item.frame_id,
                    item.label,
                    json.dumps(item.bbox, separators=(",", ":")),
                    item.confidence,
                    detector.revision,
                ),
            )
            connection.execute(
                """
                INSERT INTO detection_generation_items(
                    generation_key,block_id,ordinal,detection_id
                ) VALUES (?,?,?,?)
                """,
                (
                    checkpoint.generation_key,
                    block_id,
                    ordinal,
                    int(cursor.lastrowid),
                ),
            )
            inserted += 1
    checkpoint.complete(block_id)
    return inserted


def finalize_detection_generation(
    connection: sqlite3.Connection,
    *,
    video_id: str,
    generation_key: str,
    expected_block_ids: Sequence[str],
) -> None:
    """Atomically promote a complete bounded detection job for graph/index use."""
    expected = set(expected_block_ids)
    observed = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT i.block_id
            FROM detection_generation_items i
            JOIN detections d ON d.detection_id=i.detection_id
            JOIN frames f ON f.frame_id=d.frame_id
            WHERE i.generation_key=? AND f.video_id=?
            """,
            (generation_key, video_id),
        )
    }
    if observed != expected:
        raise ValueError(
            f"detection generation is incomplete: expected {sorted(expected)}, "
            f"observed {sorted(observed)}"
        )
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS active_detection_generations (
                video_id TEXT PRIMARY KEY,
                generation_key TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO active_detection_generations(video_id,generation_key)
            VALUES (?,?)
            ON CONFLICT(video_id) DO UPDATE
            SET generation_key=excluded.generation_key
            """,
            (video_id, generation_key),
        )
