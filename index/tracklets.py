"""Anonymous local-continuity association scoped to one camera/video session."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from .stores import content_key


@dataclass(frozen=True)
class DetectionObservation:
    detection_id: int
    frame_id: int
    video_id: str
    camera_id: str | None
    pts_seconds: float
    label: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None


@dataclass
class AnonymousTracklet:
    video_id: str
    camera_id: str | None
    label: str
    detections: list[DetectionObservation] = field(default_factory=list)
    continuity: str = "continuous"

    @property
    def session_id(self) -> str:
        """The frozen prototype schema defines each video as one tracking session."""
        return self.video_id

    @property
    def t0(self) -> float:
        return self.detections[0].pts_seconds

    @property
    def t1(self) -> float:
        return self.detections[-1].pts_seconds

    @property
    def trajectory(self) -> list[dict[str, object]]:
        return [
            {
                "detection_id": item.detection_id,
                "frame_id": item.frame_id,
                "pts_seconds": item.pts_seconds,
                "bbox": item.bbox,
                "confidence": item.confidence,
            }
            for item in self.detections
        ]


def bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    ix0, iy0 = max(left[0], right[0]), max(left[1], right[1])
    ix1, iy1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def associate_tracklets(
    observations: Iterable[DetectionObservation],
    *,
    min_iou: float = 0.2,
    max_gap_s: float = 2.0,
    continuous_gap_s: float = 1.0,
) -> list[AnonymousTracklet]:
    """Greedy local association without appearance re-identification."""
    ordered = sorted(
        observations,
        key=lambda item: (
            item.video_id,
            item.camera_id or "",
            item.pts_seconds,
            item.detection_id,
        ),
    )
    active: list[AnonymousTracklet] = []
    finished: list[AnonymousTracklet] = []
    current_scope: tuple[str, str | None] | None = None
    for observation in ordered:
        scope = (observation.video_id, observation.camera_id)
        if current_scope is not None and scope != current_scope:
            finished.extend(active)
            active = []
        current_scope = scope
        still_active = [
            track
            for track in active
            if observation.pts_seconds - track.t1 <= max_gap_s
        ]
        finished.extend(track for track in active if track not in still_active)
        active = still_active
        candidates = [
            (
                bbox_iou(track.detections[-1].bbox, observation.bbox),
                -track.t1,
                track,
            )
            for track in active
            if track.label == observation.label
            and observation.pts_seconds >= track.t1
        ]
        candidates = [candidate for candidate in candidates if candidate[0] >= min_iou]
        if candidates:
            _, _, selected = max(candidates, key=lambda item: (item[0], item[1]))
            gap = observation.pts_seconds - selected.t1
            if gap > continuous_gap_s:
                selected.continuity = "interrupted"
            selected.detections.append(observation)
        else:
            active.append(
                AnonymousTracklet(
                    video_id=observation.video_id,
                    camera_id=observation.camera_id,
                    label=observation.label,
                    detections=[observation],
                )
            )
    finished.extend(active)
    return finished


def load_observations(
    connection: sqlite3.Connection, video_id: str
) -> list[DetectionObservation]:
    active = connection.execute(
        """
        SELECT 1 FROM active_detection_generations WHERE video_id=?
        """,
        (video_id,),
    ).fetchone()
    mapped = connection.execute(
        """
        SELECT 1
        FROM detection_generation_items m
        JOIN detections d ON d.detection_id=m.detection_id
        JOIN frames f ON f.frame_id=d.frame_id
        WHERE f.video_id=? LIMIT 1
        """,
        (video_id,),
    ).fetchone()
    if active is None and mapped is not None:
        raise RuntimeError(
            "detection generation must be finalized before tracklet association"
        )
    detection_table = "active_detections" if active is not None else "detections"
    rows = connection.execute(
        f"""
        SELECT d.detection_id,d.frame_id,f.video_id,v.camera_id,f.pts_seconds,
               d.label,d.bbox_json,d.confidence
        FROM {detection_table} AS d
        JOIN frames AS f ON f.frame_id=d.frame_id
        JOIN videos AS v ON v.video_id=f.video_id
        WHERE f.video_id=?
        ORDER BY f.pts_seconds,d.detection_id
        """,
        (video_id,),
    )
    return [
        DetectionObservation(
            detection_id=int(row[0]),
            frame_id=int(row[1]),
            video_id=row[2],
            camera_id=row[3],
            pts_seconds=float(row[4]),
            label=row[5],
            bbox=tuple(json.loads(row[6])),
            confidence=row[7],
        )
        for row in rows
    ]


def persist_tracklets(
    connection: sqlite3.Connection,
    tracklets: Iterable[AnonymousTracklet],
    *,
    tracker_version: str,
) -> list[int]:
    with connection:
        return _persist_tracklets_no_transaction(
            connection, tracklets, tracker_version=tracker_version
        )


def _persist_tracklets_no_transaction(
    connection: sqlite3.Connection,
    tracklets: Iterable[AnonymousTracklet],
    *,
    tracker_version: str,
) -> list[int]:
    identifiers: list[int] = []
    for tracklet in tracklets:
        # video_id is the session boundary in the frozen schema.
        if any(
            detection.video_id != tracklet.video_id
            or detection.camera_id != tracklet.camera_id
            for detection in tracklet.detections
        ):
            raise ValueError(
                "tracklet detections may not cross camera/video session scope"
            )
        cursor = connection.execute(
            """
            INSERT INTO tracklets(
                video_id,camera_id,t0,t1,continuity,tracker_version
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                tracklet.video_id,
                tracklet.camera_id,
                tracklet.t0,
                tracklet.t1,
                tracklet.continuity,
                tracker_version,
            ),
        )
        identifier = int(cursor.lastrowid)
        identifiers.append(identifier)
        connection.executemany(
            """
            INSERT INTO tracklet_detections(tracklet_id,detection_id)
            VALUES (?,?)
            """,
            [
                (identifier, detection.detection_id)
                for detection in tracklet.detections
            ],
        )
    return identifiers


def build_tracklets(
    connection: sqlite3.Connection,
    video_id: str,
    *,
    tracker_version: str,
    min_iou: float = 0.2,
    max_gap_s: float = 2.0,
    continuous_gap_s: float = 1.0,
    enabled: bool,
) -> list[int]:
    if not enabled:
        raise RuntimeError("tracklet_lane capability is disabled")
    observations = load_observations(connection, video_id)
    generation_key = content_key(
        {
            "video_id": video_id,
            "tracker_version": tracker_version,
            "min_iou": min_iou,
            "max_gap_s": max_gap_s,
            "continuous_gap_s": continuous_gap_s,
            "detection_ids": [item.detection_id for item in observations],
        }
    )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tracklet_generations (
            generation_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            tracker_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('complete'))
        );
        CREATE TABLE IF NOT EXISTS tracklet_generation_items (
            generation_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            tracklet_id INTEGER NOT NULL,
            PRIMARY KEY(generation_key,ordinal),
            FOREIGN KEY(tracklet_id) REFERENCES tracklets(tracklet_id)
        );
        """
    )
    prior = list(
        connection.execute(
            """
            SELECT tracklet_id FROM tracklet_generation_items
            WHERE generation_key=? ORDER BY ordinal
            """,
            (generation_key,),
        )
    )
    complete = connection.execute(
        "SELECT 1 FROM tracklet_generations WHERE generation_key=?",
        (generation_key,),
    ).fetchone()
    if complete is not None:
        return [int(row[0]) for row in prior]
    tracklets = associate_tracklets(
        observations,
        min_iou=min_iou,
        max_gap_s=max_gap_s,
        continuous_gap_s=continuous_gap_s,
    )
    with connection:
        identifiers = _persist_tracklets_no_transaction(
            connection, tracklets, tracker_version=tracker_version
        )
        connection.executemany(
            """
            INSERT INTO tracklet_generation_items(
                generation_key,ordinal,tracklet_id
            ) VALUES (?,?,?)
            """,
            [
                (generation_key, ordinal, tracklet_id)
                for ordinal, tracklet_id in enumerate(identifiers)
            ],
        )
        connection.execute(
            """
            INSERT INTO tracklet_generations(
                generation_key,video_id,tracker_version,status
            ) VALUES (?,?,?,'complete')
            """,
            (generation_key, video_id, tracker_version),
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS active_tracklet_generations (
                video_id TEXT PRIMARY KEY,
                generation_key TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO active_tracklet_generations(video_id,generation_key)
            VALUES (?,?)
            ON CONFLICT(video_id) DO UPDATE
            SET generation_key=excluded.generation_key
            """,
            (video_id, generation_key),
        )
    return identifiers
