"""Incremental source-to-graph materialization with inspectable provenance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from index.stores import content_key

from .graph_store import (
    EvidenceEdge,
    EvidenceNode,
    GraphStore,
    stable_edge_id,
    stable_node_id,
)
from .provenance import EvidenceProvenance, edge_evidence


GRAPH_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class GraphBuildStats:
    generation_key: str
    nodes_written: int
    edges_written: int
    resumed_complete: bool = False


@dataclass(frozen=True)
class ObservationSafetyFields:
    """Explicit assessability; ``predicate_scope`` is the exact logic-group ID."""

    predicate_scope: str
    expected_ticks: int
    observed_ticks: int
    assessable_ticks: int
    occluded_ticks: int
    low_light_ticks: int
    region_assessable: bool
    coverage_complete: bool

    def __post_init__(self) -> None:
        if not self.predicate_scope:
            raise ValueError("predicate_scope is required for assessability evidence")
        values = (
            self.expected_ticks,
            self.observed_ticks,
            self.assessable_ticks,
            self.occluded_ticks,
            self.low_light_ticks,
        )
        if any(value < 0 for value in values):
            raise ValueError("observation safety counts cannot be negative")
        if self.observed_ticks > self.expected_ticks:
            raise ValueError("observed_ticks cannot exceed expected_ticks")
        if self.assessable_ticks > self.observed_ticks:
            raise ValueError("assessable_ticks cannot exceed observed_ticks")
        if self.occluded_ticks > self.observed_ticks:
            raise ValueError("occluded_ticks cannot exceed observed_ticks")
        if self.low_light_ticks > self.observed_ticks:
            raise ValueError("low_light_ticks cannot exceed observed_ticks")
        if self.coverage_complete != (
            self.expected_ticks > 0 and self.observed_ticks == self.expected_ticks
        ):
            raise ValueError(
                "coverage_complete must exactly reflect the declared tick ledger"
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "predicate_scope": self.predicate_scope,
            "expected_ticks": self.expected_ticks,
            "observed_ticks": self.observed_ticks,
            "assessable_ticks": self.assessable_ticks,
            "occluded_ticks": self.occluded_ticks,
            "low_light_ticks": self.low_light_ticks,
            "region_assessable": self.region_assessable,
            "assessable": self.region_assessable
            and self.assessable_ticks > 0
            and self.occluded_ticks < self.observed_ticks
            and self.low_light_ticks < self.observed_ticks,
            "coverage_complete": self.coverage_complete,
            "occlusion_assessed": True,
        }


class GraphBuilder:
    def __init__(
        self,
        store: GraphStore,
        *,
        producer_version: str,
        sampling_policy_version: str,
        detector_version: str | None,
        tracker_version: str | None,
        thresholds: dict[str, Any],
        graph_schema_version: str = GRAPH_SCHEMA_VERSION,
    ):
        self.store = store
        self.producer_version = producer_version
        self.inputs = {
            "sampling_policy_version": sampling_policy_version,
            "detector_version": detector_version,
            "tracker_version": tracker_version,
            "thresholds": thresholds,
            "graph_schema_version": graph_schema_version,
        }

    def generation_key(self, video_id: str, source_sha256: str) -> str:
        return content_key(
            {
                "video_id": video_id,
                "source_sha256": source_sha256,
                **self.inputs,
            }
        )

    def _provenance(
        self,
        video_id: str,
        source_hash: str,
        t0: float,
        t1: float,
        frame_ids: Iterable[int],
        generation_key: str,
    ) -> dict[str, Any]:
        return EvidenceProvenance(
            video_id=video_id,
            source_sha256=source_hash,
            t0=t0,
            t1=t1,
            producer_version=self.producer_version,
            frame_ids=tuple(frame_ids),
            artifact_generation=generation_key,
        ).as_dict()

    def build_video(self, video_id: str, *, enabled: bool = True) -> GraphBuildStats:
        if not enabled:
            raise RuntimeError("evidence_graph capability is disabled")
        video = self.store.connection.execute(
            "SELECT * FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()
        if video is None:
            raise KeyError(video_id)
        source_hash = video["sha256"]
        generation = self.generation_key(video_id, source_hash)
        active_detection = self.store.connection.execute(
            """
            SELECT 1 FROM active_detection_generations WHERE video_id=?
            """,
            (video_id,),
        ).fetchone()
        mapped_detection = self.store.connection.execute(
            """
            SELECT 1
            FROM detection_generation_items m
            JOIN detections d ON d.detection_id=m.detection_id
            JOIN frames f ON f.frame_id=d.frame_id
            WHERE f.video_id=? LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        detection_table = (
            "active_detections"
            if active_detection is not None
            else (None if mapped_detection is not None else "detections")
        )
        active_tracklet = self.store.connection.execute(
            """
            SELECT 1 FROM active_tracklet_generations WHERE video_id=?
            """,
            (video_id,),
        ).fetchone()
        mapped_tracklet = self.store.connection.execute(
            """
            SELECT 1
            FROM tracklet_generation_items m
            JOIN tracklets t ON t.tracklet_id=m.tracklet_id
            WHERE t.video_id=? LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        tracklet_table = (
            "active_tracklets"
            if active_tracklet is not None
            else (None if mapped_tracklet is not None else "tracklets")
        )
        prior = self.store.connection.execute(
            "SELECT status FROM graph_generations WHERE generation_key=?",
            (generation,),
        ).fetchone()
        if prior and prior["status"] == "complete":
            return GraphBuildStats(generation, 0, 0, resumed_complete=True)
        with self.store.connection:
            self.store.connection.execute(
                """
                INSERT INTO graph_generations(
                    generation_key,video_id,inputs_json,status,producer_version,error
                ) VALUES (?,?,?,'building',?,NULL)
                ON CONFLICT(generation_key) DO UPDATE SET status='building',error=NULL
                """,
                (
                    generation,
                    video_id,
                    json.dumps(self.inputs, sort_keys=True, separators=(",", ":")),
                    self.producer_version,
                ),
            )
        nodes = 0
        edges = 0
        try:
            node_map: dict[tuple[str, int], str] = {}
            # Mandatory minimal graph lane.
            for row in self.store.connection.execute(
                "SELECT * FROM frames WHERE video_id=? ORDER BY frame_id", (video_id,)
            ):
                node_id = stable_node_id("frame", video_id, row["frame_id"], generation)
                payload = {
                    "frame_id": row["frame_id"],
                    "frame_ids": [row["frame_id"]],
                    "camera_id": video["camera_id"],
                    "sampling_lane": row["sampling_lane"],
                    "luminance": row["luminance"],
                    "sharpness": row["sharpness"],
                    "motion_score": row["motion_score"],
                    "embedding_row": row["frame_embedding_row"],
                    "thumbnail_path": row["thumbnail_path"],
                    "provenance": self._provenance(
                        video_id,
                        source_hash,
                        row["pts_seconds"],
                        row["pts_seconds"],
                        [row["frame_id"]],
                        generation,
                    ),
                }
                self.store.put_node(
                    EvidenceNode(
                        node_id,
                        "frame",
                        video_id,
                        row["pts_seconds"],
                        row["pts_seconds"],
                        payload,
                        self.producer_version,
                    )
                )
                node_map[("frame", row["frame_id"])] = node_id
                nodes += 1
            for table, kind, id_column in (
                ("windows", "window", "window_id"),
                ("clips", "clip", "clip_id"),
            ):
                for row in self.store.connection.execute(
                    f"SELECT * FROM {table} WHERE video_id=? ORDER BY {id_column}",
                    (video_id,),
                ):
                    source_id = row[id_column]
                    node_id = stable_node_id(kind, video_id, source_id, generation)
                    contained_frames = [
                        int(item[0])
                        for item in self.store.connection.execute(
                            """
                            SELECT frame_id FROM frames
                            WHERE video_id=? AND pts_seconds>=? AND pts_seconds<?
                            ORDER BY frame_id
                            """,
                            (video_id, row["t0"], row["t1"]),
                        )
                    ]
                    coverage_payload: dict[str, Any] = {}
                    if kind == "window":
                        coverage = self.store.connection.execute(
                            """
                            SELECT COUNT(*) AS expected,
                                   SUM(t.status='embedded') AS observed,
                                   SUM(
                                     t.status='embedded'
                                     AND (? IS NULL OR f.luminance>=?)
                                     AND (? IS NULL OR f.sharpness>=?)
                                   ) AS assessable,
                                   SUM(
                                     t.status='embedded'
                                     AND ? IS NOT NULL AND f.luminance<?
                                   ) AS low_light
                            FROM sample_ticks t
                            LEFT JOIN frames f ON f.frame_id=t.frame_id
                            WHERE t.video_id=? AND t.target_pts>=? AND t.target_pts<?
                              AND t.sampling_lane IN ('base','motion')
                            """,
                            (
                                self.inputs["thresholds"].get("min_luminance"),
                                self.inputs["thresholds"].get("min_luminance"),
                                self.inputs["thresholds"].get("min_sharpness"),
                                self.inputs["thresholds"].get("min_sharpness"),
                                self.inputs["thresholds"].get("min_luminance"),
                                self.inputs["thresholds"].get("min_luminance"),
                                video_id,
                                row["t0"],
                                row["t1"],
                            ),
                        ).fetchone()
                        expected = int(coverage["expected"] or 0)
                        observed = int(coverage["observed"] or 0)
                        coverage_payload = {
                            "expected_ticks": expected,
                            "observed_ticks": observed,
                            # Image quality is useful telemetry, but cannot prove
                            # query-region visibility or absence on its own.
                            "assessable_ticks": 0,
                            "quality_assessable_ticks": int(
                                coverage["assessable"] or 0
                            ),
                            "low_light_ticks": int(coverage["low_light"] or 0),
                            "occluded_ticks": 0,
                            "occlusion_assessed": False,
                            "region_assessable": False,
                            "coverage_complete": expected > 0 and observed == expected,
                            "assessable": False,
                        }
                    self.store.put_node(
                        EvidenceNode(
                            node_id,
                            kind,
                            video_id,
                            row["t0"],
                            row["t1"],
                            {
                                **dict(row),
                                "camera_id": video["camera_id"],
                                "frame_ids": contained_frames,
                                **coverage_payload,
                                "provenance": self._provenance(
                                    video_id,
                                    source_hash,
                                    row["t0"],
                                    row["t1"],
                                    contained_frames,
                                    generation,
                                ),
                            },
                            self.producer_version,
                        )
                    )
                    node_map[(kind, source_id)] = node_id
                    nodes += 1
                    for frame_id in contained_frames:
                        frame_node = node_map.get(("frame", frame_id))
                        if frame_node is None:
                            continue
                        evidence = edge_evidence(
                            video_id=video_id,
                            source_sha256=source_hash,
                            t0=row["t0"],
                            t1=row["t1"],
                            producer_version=self.producer_version,
                            frame_ids=[frame_id],
                            relationship_basis="PTS containment",
                            artifact_generation=generation,
                        )
                        self.store.put_edge(
                            EvidenceEdge(
                                stable_edge_id(
                                    node_id, "contains", frame_node, generation
                                ),
                                node_id,
                                "contains",
                                frame_node,
                                evidence,
                                self.producer_version,
                                row["t0"],
                                row["t1"],
                            )
                        )
                        edges += 1
            detection_rows = (
                ()
                if detection_table is None
                else self.store.connection.execute(
                    f"""
                    SELECT d.*,f.video_id,f.pts_seconds
                    FROM {detection_table} d
                    JOIN frames f ON f.frame_id=d.frame_id
                    WHERE f.video_id=? ORDER BY d.detection_id
                    """,
                    (video_id,),
                )
            )
            for row in detection_rows:
                node_id = stable_node_id(
                    "detection", video_id, row["detection_id"], generation
                )
                self.store.put_node(
                    EvidenceNode(
                        node_id,
                        "detection",
                        video_id,
                        row["pts_seconds"],
                        row["pts_seconds"],
                        {
                            "detection_id": row["detection_id"],
                            "frame_id": row["frame_id"],
                            "frame_ids": [row["frame_id"]],
                            "camera_id": video["camera_id"],
                            "label": row["label"],
                            "bbox": json.loads(row["bbox_json"]),
                            "confidence": row["confidence"],
                            "detector_version": row["detector_version"],
                            "provenance": self._provenance(
                                video_id,
                                source_hash,
                                row["pts_seconds"],
                                row["pts_seconds"],
                                [row["frame_id"]],
                                generation,
                            ),
                        },
                        self.producer_version,
                    )
                )
                node_map[("detection", row["detection_id"])] = node_id
                nodes += 1
                frame_node = node_map.get(("frame", row["frame_id"]))
                if frame_node is not None:
                    evidence = edge_evidence(
                        video_id=video_id,
                        source_sha256=source_hash,
                        t0=row["pts_seconds"],
                        t1=row["pts_seconds"],
                        producer_version=self.producer_version,
                        frame_ids=[row["frame_id"]],
                        relationship_basis="detector source frame",
                        artifact_generation=generation,
                    )
                    self.store.put_edge(
                        EvidenceEdge(
                            stable_edge_id(
                                frame_node, "contains", node_id, generation
                            ),
                            frame_node,
                            "contains",
                            node_id,
                            evidence,
                            self.producer_version,
                            row["pts_seconds"],
                            row["pts_seconds"],
                        )
                    )
                    edges += 1
            # Same-frame co-occurrence is visible evidence, not an identity claim.
            cooccurrence_rows = (
                ()
                if detection_table is None
                else self.store.connection.execute(
                    f"""
                    SELECT a.detection_id AS left_id,b.detection_id AS right_id,
                           a.frame_id,f.pts_seconds
                    FROM {detection_table} a
                    JOIN {detection_table} b
                      ON b.frame_id=a.frame_id AND b.detection_id>a.detection_id
                    JOIN frames f ON f.frame_id=a.frame_id
                    WHERE f.video_id=?
                    ORDER BY a.frame_id,a.detection_id,b.detection_id
                    """,
                    (video_id,),
                )
            )
            for row in cooccurrence_rows:
                left_node = node_map[("detection", row["left_id"])]
                right_node = node_map[("detection", row["right_id"])]
                evidence = edge_evidence(
                    video_id=video_id,
                    source_sha256=source_hash,
                    t0=row["pts_seconds"],
                    t1=row["pts_seconds"],
                    producer_version=self.producer_version,
                    frame_ids=[row["frame_id"]],
                    relationship_basis="same decoded frame",
                    artifact_generation=generation,
                )
                for subject, object_node in (
                    (left_node, right_node),
                    (right_node, left_node),
                ):
                    self.store.put_edge(
                        EvidenceEdge(
                            stable_edge_id(
                                subject, "co_occurs", object_node, generation
                            ),
                            subject,
                            "co_occurs",
                            object_node,
                            evidence,
                            self.producer_version,
                            row["pts_seconds"],
                            row["pts_seconds"],
                        )
                    )
                    edges += 1
            tracklet_rows = (
                ()
                if tracklet_table is None
                else self.store.connection.execute(
                    f"""
                    SELECT * FROM {tracklet_table}
                    WHERE video_id=? ORDER BY tracklet_id
                    """,
                    (video_id,),
                )
            )
            for row in tracklet_rows:
                observations = list(
                    self.store.connection.execute(
                        """
                        SELECT d.detection_id,d.frame_id,f.pts_seconds
                        FROM tracklet_detections td
                        JOIN detections d ON d.detection_id=td.detection_id
                        JOIN frames f ON f.frame_id=d.frame_id
                        WHERE td.tracklet_id=? ORDER BY f.pts_seconds
                        """,
                        (row["tracklet_id"],),
                    )
                )
                frame_ids = [int(item["frame_id"]) for item in observations]
                node_id = stable_node_id(
                    "tracklet", video_id, row["tracklet_id"], generation
                )
                self.store.put_node(
                    EvidenceNode(
                        node_id,
                        "tracklet",
                        video_id,
                        row["t0"],
                        row["t1"],
                        {
                            **dict(row),
                            "session_id": row["video_id"],
                            "frame_ids": frame_ids,
                            "anonymous_local_continuity_only": True,
                            "detection_ids": [
                                item["detection_id"] for item in observations
                            ],
                            "trajectory": [
                                {
                                    "frame_id": item["frame_id"],
                                    "pts_seconds": item["pts_seconds"],
                                }
                                for item in observations
                            ],
                            "provenance": self._provenance(
                                video_id,
                                source_hash,
                                row["t0"],
                                row["t1"],
                                frame_ids,
                                generation,
                            ),
                        },
                        self.producer_version,
                    )
                )
                node_map[("tracklet", row["tracklet_id"])] = node_id
                nodes += 1
                for item in observations:
                    detection_node = node_map.get(
                        ("detection", item["detection_id"])
                    )
                    if detection_node is None:
                        continue
                    evidence = edge_evidence(
                        video_id=video_id,
                        source_sha256=source_hash,
                        t0=item["pts_seconds"],
                        t1=item["pts_seconds"],
                        producer_version=self.producer_version,
                        frame_ids=[item["frame_id"]],
                        relationship_basis="tracker association",
                        artifact_generation=generation,
                    )
                    self.store.put_edge(
                        EvidenceEdge(
                            stable_edge_id(
                                detection_node,
                                "belongs_to_track",
                                node_id,
                                generation,
                            ),
                            detection_node,
                            "belongs_to_track",
                            node_id,
                            evidence,
                            self.producer_version,
                            item["pts_seconds"],
                            item["pts_seconds"],
                        )
                    )
                    edges += 1
            nodes_added, edges_added = self._build_text_nodes(
                video_id,
                video["camera_id"],
                source_hash,
                generation,
                node_map,
            )
            nodes += nodes_added
            edges += edges_added
            with self.store.connection:
                self.store.connection.execute(
                    """
                    UPDATE graph_generations SET status='complete',error=NULL
                    WHERE generation_key=?
                    """,
                    (generation,),
                )
                self.store.connection.execute(
                    """
                    INSERT INTO active_graph_generations(video_id,generation_key)
                    VALUES (?,?)
                    ON CONFLICT(video_id) DO UPDATE
                    SET generation_key=excluded.generation_key
                    """,
                    (video_id, generation),
                )
            return GraphBuildStats(generation, nodes, edges)
        except Exception as exc:
            with self.store.connection:
                self.store.connection.execute(
                    """
                    UPDATE graph_generations SET status='failed',error=?
                    WHERE generation_key=?
                    """,
                    (f"{type(exc).__name__}: {exc}", generation),
                )
            raise

    def _build_text_nodes(
        self,
        video_id: str,
        camera_id: str | None,
        source_hash: str,
        generation: str,
        node_map: dict[tuple[str, int], str],
    ) -> tuple[int, int]:
        nodes = edges = 0
        for row in self.store.connection.execute(
            """
            SELECT o.*,f.pts_seconds FROM ocr_hits o
            JOIN frames f ON f.frame_id=o.frame_id
            WHERE f.video_id=? ORDER BY o.ocr_id
            """,
            (video_id,),
        ):
            node_id = stable_node_id("ocr", video_id, row["ocr_id"], generation)
            self.store.put_node(
                EvidenceNode(
                    node_id,
                    "ocr",
                    video_id,
                    row["pts_seconds"],
                    row["pts_seconds"],
                    {
                        "ocr_id": row["ocr_id"],
                        "frame_id": row["frame_id"],
                        "frame_ids": [row["frame_id"]],
                        "camera_id": camera_id,
                        "quoted_text": row["text"],
                        "confidence": row["confidence"],
                        "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                        "provenance": self._provenance(
                            video_id,
                            source_hash,
                            row["pts_seconds"],
                            row["pts_seconds"],
                            [row["frame_id"]],
                            generation,
                        ),
                    },
                    self.producer_version,
                )
            )
            nodes += 1
            frame_node = node_map.get(("frame", row["frame_id"]))
            if frame_node is not None:
                evidence = edge_evidence(
                    video_id=video_id,
                    source_sha256=source_hash,
                    t0=row["pts_seconds"],
                    t1=row["pts_seconds"],
                    producer_version=self.producer_version,
                    frame_ids=[row["frame_id"]],
                    relationship_basis="OCR source frame",
                    artifact_generation=generation,
                )
                self.store.put_edge(
                    EvidenceEdge(
                        stable_edge_id(
                            frame_node, "contains", node_id, generation
                        ),
                        frame_node,
                        "contains",
                        node_id,
                        evidence,
                        self.producer_version,
                        row["pts_seconds"],
                        row["pts_seconds"],
                    )
                )
                edges += 1
        for row in self.store.connection.execute(
            "SELECT * FROM asr_segments WHERE video_id=? ORDER BY asr_id",
            (video_id,),
        ):
            node_id = stable_node_id("asr", video_id, row["asr_id"], generation)
            self.store.put_node(
                EvidenceNode(
                    node_id,
                    "asr",
                    video_id,
                    row["t0"],
                    row["t1"],
                    {
                        **dict(row),
                        "camera_id": camera_id,
                        "quoted_text": row["text"],
                        "provenance": self._provenance(
                            video_id,
                            source_hash,
                            row["t0"],
                            row["t1"],
                            [],
                            generation,
                        ),
                    },
                    self.producer_version,
                )
            )
            nodes += 1
        return nodes, edges

    def put_episode(
        self,
        *,
        video_id: str,
        episode_id: str,
        t0: float,
        t1: float,
        payload: dict[str, Any],
        observation_safety: ObservationSafetyFields | None = None,
    ) -> str:
        """Allow the assembly owner to persist a typed episode without internals."""
        video = self.store.connection.execute(
            "SELECT sha256,camera_id FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()
        if video is None:
            raise KeyError(video_id)
        generation = self.generation_key(video_id, video["sha256"])
        active = self.store.connection.execute(
            """
            SELECT generation_key FROM active_graph_generations WHERE video_id=?
            """,
            (video_id,),
        ).fetchone()
        if active is None:
            self.build_video(video_id)
            active = self.store.connection.execute(
                """
                SELECT generation_key FROM active_graph_generations WHERE video_id=?
                """,
                (video_id,),
            ).fetchone()
        if active is None or active["generation_key"] != generation:
            raise ValueError(
                "episode producer generation is not the active graph generation"
            )
        node_id = stable_node_id("episode", video_id, episode_id, generation)
        evidence_frames = tuple(int(value) for value in payload.get("frame_ids", []))
        safety_keys = {
            "predicate_scope",
            "expected_ticks",
            "observed_ticks",
            "assessable_ticks",
            "occluded_ticks",
            "low_light_ticks",
            "region_assessable",
            "coverage_complete",
            "occlusion_assessed",
            "assessable",
        }
        if observation_safety is None and safety_keys.intersection(payload):
            raise ValueError(
                "observation safety fields must use ObservationSafetyFields"
            )
        complete_payload = {
            **payload,
            "camera_id": payload.get("camera_id", video["camera_id"]),
            "frame_ids": list(evidence_frames),
            **(
                observation_safety.as_payload()
                if observation_safety is not None
                else {}
            ),
            "provenance": self._provenance(
                video_id,
                video["sha256"],
                t0,
                t1,
                evidence_frames,
                generation,
            ),
        }
        self.store.put_node(
            EvidenceNode(
                node_id,
                "episode",
                video_id,
                t0,
                t1,
                complete_payload,
                self.producer_version,
            )
        )
        visible = self.store.connection.execute(
            """
            SELECT 1 FROM active_evidence_nodes WHERE node_id=?
            """,
            (node_id,),
        ).fetchone()
        if visible is None:
            raise AssertionError("episode was not mapped into the active graph generation")
        return node_id
