"""Typed SQLite temporal evidence graph and canonical metadata schema."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from ingest.sampler import INGEST_SCHEMA

from .provenance import canonical_hash, validate_inspectable_evidence


NodeType = Literal[
    "frame", "window", "clip", "detection", "tracklet", "ocr", "asr", "episode"
]
Predicate = Literal[
    "overlaps",
    "precedes",
    "follows",
    "near",
    "contains",
    "belongs_to_track",
    "carries",
    "enters",
    "exits",
    "co_occurs",
]

NODE_TYPES = {
    "frame",
    "window",
    "clip",
    "detection",
    "tracklet",
    "ocr",
    "asr",
    "episode",
}
PREDICATES = {
    "overlaps",
    "precedes",
    "follows",
    "near",
    "contains",
    "belongs_to_track",
    "carries",
    "enters",
    "exits",
    "co_occurs",
}


GRAPH_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS clips (
    clip_id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    clip_embedding_row INTEGER,
    embedding_model_version TEXT
);
CREATE TABLE IF NOT EXISTS detections (
    detection_id INTEGER PRIMARY KEY,
    frame_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    bbox_json TEXT NOT NULL,
    confidence REAL,
    detector_version TEXT NOT NULL,
    FOREIGN KEY(frame_id) REFERENCES frames(frame_id)
);
CREATE TABLE IF NOT EXISTS detection_generation_items (
    generation_key TEXT NOT NULL,
    block_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    detection_id INTEGER NOT NULL,
    PRIMARY KEY(generation_key,block_id,ordinal),
    FOREIGN KEY(detection_id) REFERENCES detections(detection_id)
);
CREATE TABLE IF NOT EXISTS active_detection_generations (
    video_id TEXT PRIMARY KEY,
    generation_key TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS active_detections AS
SELECT d.*
FROM detections d
JOIN detection_generation_items m ON m.detection_id=d.detection_id
JOIN frames f ON f.frame_id=d.frame_id
JOIN active_detection_generations a
  ON a.video_id=f.video_id AND a.generation_key=m.generation_key;
CREATE TABLE IF NOT EXISTS tracklets (
    tracklet_id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    camera_id TEXT,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    continuity TEXT NOT NULL CHECK(continuity IN ('continuous','interrupted')),
    tracker_version TEXT NOT NULL,
    FOREIGN KEY(video_id) REFERENCES videos(video_id)
);
CREATE TABLE IF NOT EXISTS tracklet_detections (
    tracklet_id INTEGER NOT NULL,
    detection_id INTEGER NOT NULL,
    PRIMARY KEY(tracklet_id,detection_id),
    FOREIGN KEY(tracklet_id) REFERENCES tracklets(tracklet_id),
    FOREIGN KEY(detection_id) REFERENCES detections(detection_id)
);
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
CREATE TABLE IF NOT EXISTS active_tracklet_generations (
    video_id TEXT PRIMARY KEY,
    generation_key TEXT NOT NULL,
    FOREIGN KEY(generation_key) REFERENCES tracklet_generations(generation_key)
);
CREATE VIEW IF NOT EXISTS active_tracklets AS
SELECT t.*
FROM tracklets t
JOIN tracklet_generation_items m ON m.tracklet_id=t.tracklet_id
JOIN active_tracklet_generations a
  ON a.video_id=t.video_id AND a.generation_key=m.generation_key;
CREATE TABLE IF NOT EXISTS evidence_nodes (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL CHECK(node_type IN (
        'frame','window','clip','detection','tracklet','ocr','asr','episode'
    )),
    video_id TEXT NOT NULL,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    payload_json TEXT NOT NULL,
    producer_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id TEXT PRIMARY KEY,
    subject_node_id TEXT NOT NULL,
    predicate TEXT NOT NULL CHECK(predicate IN (
        'overlaps','precedes','follows','near','contains',
        'belongs_to_track','carries','enters','exits','co_occurs'
    )),
    object_node_id TEXT NOT NULL,
    t0 REAL,
    t1 REAL,
    evidence_json TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    FOREIGN KEY(subject_node_id) REFERENCES evidence_nodes(node_id),
    FOREIGN KEY(object_node_id) REFERENCES evidence_nodes(node_id)
);
CREATE TABLE IF NOT EXISTS ocr_hits (
    ocr_id INTEGER PRIMARY KEY,
    frame_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    bbox_json TEXT
);
CREATE TABLE IF NOT EXISTS asr_segments (
    asr_id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    text TEXT NOT NULL,
    language TEXT,
    asr_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_cache (
    cache_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
    export_id TEXT PRIMARY KEY,
    search_id TEXT,
    match_json TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_generations (
    generation_key TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('building','complete','failed')),
    producer_version TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS graph_generation_nodes (
    generation_key TEXT NOT NULL,
    node_id TEXT NOT NULL,
    PRIMARY KEY(generation_key,node_id),
    FOREIGN KEY(generation_key) REFERENCES graph_generations(generation_key),
    FOREIGN KEY(node_id) REFERENCES evidence_nodes(node_id)
);
CREATE TABLE IF NOT EXISTS graph_generation_edges (
    generation_key TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    PRIMARY KEY(generation_key,edge_id),
    FOREIGN KEY(generation_key) REFERENCES graph_generations(generation_key),
    FOREIGN KEY(edge_id) REFERENCES evidence_edges(edge_id)
);
CREATE TABLE IF NOT EXISTS active_graph_generations (
    video_id TEXT PRIMARY KEY,
    generation_key TEXT NOT NULL,
    FOREIGN KEY(generation_key) REFERENCES graph_generations(generation_key)
);
CREATE VIEW IF NOT EXISTS active_evidence_nodes AS
SELECT n.*
FROM evidence_nodes n
JOIN graph_generation_nodes m ON m.node_id=n.node_id
JOIN active_graph_generations a ON a.generation_key=m.generation_key;
CREATE VIEW IF NOT EXISTS active_evidence_edges AS
SELECT e.*
FROM evidence_edges e
JOIN graph_generation_edges m ON m.edge_id=e.edge_id
JOIN active_graph_generations a ON a.generation_key=m.generation_key;
CREATE INDEX IF NOT EXISTS idx_clips_video_time ON clips(video_id,t0,t1);
CREATE INDEX IF NOT EXISTS idx_detections_frame ON detections(frame_id);
CREATE INDEX IF NOT EXISTS idx_tracklets_scope_time
    ON tracklets(camera_id,video_id,t0,t1);
CREATE INDEX IF NOT EXISTS idx_nodes_scope_type_time
    ON evidence_nodes(video_id,node_type,t0,t1);
CREATE INDEX IF NOT EXISTS idx_edges_subject_predicate
    ON evidence_edges(subject_node_id,predicate);
CREATE INDEX IF NOT EXISTS idx_edges_object_predicate
    ON evidence_edges(object_node_id,predicate);
"""


@dataclass(frozen=True)
class EvidenceNode:
    node_id: str
    node_type: NodeType
    video_id: str
    t0: float
    t1: float
    payload: Mapping[str, Any]
    producer_version: str

    def __post_init__(self) -> None:
        if self.node_type not in NODE_TYPES:
            raise ValueError(f"invalid evidence node type: {self.node_type}")
        if self.t0 < 0 or self.t1 < self.t0:
            raise ValueError("invalid node interval")


@dataclass(frozen=True)
class EvidenceEdge:
    edge_id: str
    subject_node_id: str
    predicate: Predicate
    object_node_id: str
    evidence: Mapping[str, Any]
    producer_version: str
    t0: float | None = None
    t1: float | None = None

    def __post_init__(self) -> None:
        if self.predicate not in PREDICATES:
            raise ValueError(f"invalid evidence predicate: {self.predicate}")
        if self.t0 is not None and self.t0 < 0:
            raise ValueError("edge t0 cannot be negative")
        if self.t0 is not None and self.t1 is not None and self.t1 < self.t0:
            raise ValueError("invalid edge interval")
        validate_inspectable_evidence(self.evidence)


def stable_node_id(
    node_type: str, video_id: str, source_identifier: str | int, generation_key: str
) -> str:
    digest = canonical_hash(
        {
            "node_type": node_type,
            "video_id": video_id,
            "source_identifier": str(source_identifier),
            "generation_key": generation_key,
        }
    )
    return f"{node_type}:{digest}"


def stable_edge_id(
    subject_node_id: str,
    predicate: str,
    object_node_id: str,
    generation_key: str,
) -> str:
    digest = canonical_hash(
        {
            "subject": subject_node_id,
            "predicate": predicate,
            "object": object_node_id,
            "generation_key": generation_key,
        }
    )
    return f"edge:{digest}"


class GraphStore:
    def __init__(self, database: str | Path | sqlite3.Connection):
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        self.connection = (
            sqlite3.connect(str(database))
            if self._owns_connection
            else database
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(INGEST_SCHEMA)
        self._batch_depth = 0
        self.connection.executescript(GRAPH_SCHEMA)

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection:
            yield self.connection

    @contextmanager
    def batch(self) -> Iterator[sqlite3.Connection]:
        """Group many put_node/put_edge writes into one transaction.

        Committing per row made graph materialisation fsync-bound: a ten-minute
        source at 1 fps is tens of thousands of independent commits, each with a
        read-back SELECT. Independent commits also left partial nodes and edges
        behind when a build failed midway, because the generation was marked
        'failed' after the rows were already durable.
        """
        self._batch_depth += 1
        try:
            if self._batch_depth == 1:
                with self.connection:
                    yield self.connection
            else:
                yield self.connection
        finally:
            self._batch_depth -= 1

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Own the transaction only when not already inside a batch."""
        if self._batch_depth:
            yield self.connection
        else:
            with self.connection:
                yield self.connection

    def put_node(self, node: EvidenceNode) -> None:
        payload = json.dumps(node.payload, sort_keys=True, separators=(",", ":"))
        with self._write():
            self.connection.execute(
                """
                INSERT INTO evidence_nodes(
                    node_id,node_type,video_id,t0,t1,payload_json,producer_version
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO NOTHING
                """,
                (
                    node.node_id,
                    node.node_type,
                    node.video_id,
                    node.t0,
                    node.t1,
                    payload,
                    node.producer_version,
                ),
            )
            existing = self.connection.execute(
                "SELECT * FROM evidence_nodes WHERE node_id=?", (node.node_id,)
            ).fetchone()
            expected = (
                node.node_type,
                node.video_id,
                node.t0,
                node.t1,
                payload,
                node.producer_version,
            )
            actual = tuple(existing[key] for key in (
                "node_type","video_id","t0","t1","payload_json","producer_version"
            ))
            if actual != expected:
                raise ValueError(f"node ID collision for {node.node_id}")
            generation = (
                node.payload.get("provenance", {}).get("artifact_generation")
                if isinstance(node.payload.get("provenance"), Mapping)
                else None
            )
            if generation:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO graph_generation_nodes(
                        generation_key,node_id
                    ) VALUES (?,?)
                    """,
                    (generation, node.node_id),
                )

    def put_edge(self, edge: EvidenceEdge) -> None:
        evidence = json.dumps(edge.evidence, sort_keys=True, separators=(",", ":"))
        with self._write():
            endpoints = self.connection.execute(
                """
                SELECT COUNT(*) FROM evidence_nodes WHERE node_id IN (?,?)
                """,
                (edge.subject_node_id, edge.object_node_id),
            ).fetchone()[0]
            expected_endpoints = 1 if edge.subject_node_id == edge.object_node_id else 2
            if endpoints != expected_endpoints:
                raise KeyError("both evidence edge endpoints must exist")
            self.connection.execute(
                """
                INSERT INTO evidence_edges(
                    edge_id,subject_node_id,predicate,object_node_id,t0,t1,
                    evidence_json,producer_version
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(edge_id) DO NOTHING
                """,
                (
                    edge.edge_id,
                    edge.subject_node_id,
                    edge.predicate,
                    edge.object_node_id,
                    edge.t0,
                    edge.t1,
                    evidence,
                    edge.producer_version,
                ),
            )
            existing = self.connection.execute(
                "SELECT * FROM evidence_edges WHERE edge_id=?", (edge.edge_id,)
            ).fetchone()
            expected = (
                edge.subject_node_id,
                edge.predicate,
                edge.object_node_id,
                edge.t0,
                edge.t1,
                evidence,
                edge.producer_version,
            )
            actual = tuple(existing[key] for key in (
                "subject_node_id","predicate","object_node_id","t0","t1",
                "evidence_json","producer_version"
            ))
            if actual != expected:
                raise ValueError(f"edge ID collision for {edge.edge_id}")
            generation = edge.evidence.get("artifact_generation")
            if generation:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO graph_generation_edges(
                        generation_key,edge_id
                    ) VALUES (?,?)
                    """,
                    (generation, edge.edge_id),
                )

    def node(self, node_id: str) -> EvidenceNode | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return EvidenceNode(
            node_id=row["node_id"],
            node_type=row["node_type"],
            video_id=row["video_id"],
            t0=row["t0"],
            t1=row["t1"],
            payload=json.loads(row["payload_json"]),
            producer_version=row["producer_version"],
        )
