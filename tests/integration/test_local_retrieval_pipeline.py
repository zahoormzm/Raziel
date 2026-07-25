from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from api.main import RazielService, create_app
from api.pipeline import LocalRetrievalPipeline
from index.stores import ArtifactGeneration, VersionedEmbeddingStore
from ingest.sampler import INGEST_SCHEMA
from packages.contracts.search_result import ArchiveConclusion
from packages.contracts.verification import ConstraintEvidence, VerificationResult


class DeterministicTextEncoder:
    dimension = 2
    revision = "test-text-encoder"

    def encode_text(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _text in texts]


class NegativeTextEncoder(DeterministicTextEncoder):
    revision = "test-negative-text-encoder"

    def encode_text(self, texts: list[str]) -> list[list[float]]:
        return [[-1.0, 0.0] for _text in texts]


def _build_archive(tmp_path: Path) -> tuple[Path, Path, Path]:
    database = tmp_path / "archive.sqlite3"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture-media")
    connection = sqlite3.connect(database)
    connection.executescript(INGEST_SCHEMA)
    connection.execute(
        """
        INSERT INTO videos(
            video_id,path,camera_id,sha256,ffprobe_json,timebase,duration_s,
            pipeline_version,cache_key
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            "video-1",
            str(source),
            "gate-01",
            "a" * 64,
            "{}",
            "1/1000",
            8.0,
            "sampling-v1",
            "fixture-cache",
        ),
    )
    for frame_id, pts in enumerate((1.0, 3.0, 5.0), start=1):
        connection.execute(
            """
            INSERT INTO frames(
                frame_id,video_id,pts_seconds,sampling_lane,decode_ok,
                frame_embedding_row
            ) VALUES (?,?,?,?,?,?)
            """,
            (frame_id, "video-1", pts, "base", 1, frame_id - 1),
        )
        connection.execute(
            """
            INSERT INTO sample_ticks(
                video_id,target_pts,sampling_lane,frame_id,status,error_code
            ) VALUES (?,?,?,?,?,?)
            """,
            ("video-1", pts, "base", frame_id, "embedded", None),
        )
    connection.executemany(
        "INSERT INTO windows(video_id,scale_s,t0,t1) VALUES (?,?,?,?)",
        (("video-1", 4, 0.0, 4.0), ("video-1", 4, 4.0, 8.0)),
    )
    connection.commit()
    connection.close()

    generation = ArtifactGeneration(
        schema_version="1.0",
        kind="frame_embeddings",
        inputs={"fixture": "local-pipeline"},
    )
    store_path = generation.directory(tmp_path / "stores")
    store = VersionedEmbeddingStore(
        store_path,
        dimension=2,
        generation=generation,
    )
    store.append(((1.0, 0.0), (0.9, 0.1), (1.0, 0.0)))
    store.finalize()
    return database, store_path, source


def test_live_retrieval_is_exhaustive_but_never_self_verifies(tmp_path: Path) -> None:
    database, store_path, _source = _build_archive(tmp_path)
    pipeline = LocalRetrievalPipeline(
        database=database,
        frame_store=store_path,
        encoder=DeterministicTextEncoder(),
        thresholds={
            "whole_query_frame": 0.5,
            "candidate_anchor_frame": 0.5,
            "rare_attribute_frame": 0.5,
        },
        window_scales=(4,),
        operating_point_label="synthetic-test",
    )
    try:
        result = pipeline.query(
            {
                "text": "a red object",
                "camera_ids": ["gate-01"],
                "start_time": 0,
                "end_time": 8,
            }
        )
        scoped = pipeline.query(
            {
                "text": "a red object",
                "camera_ids": ["gate-01"],
                "start_time": 2,
                "end_time": 6,
            }
        )
    finally:
        pipeline.close()

    assert result.indexing.expected_ticks == 3
    assert result.indexing.scored_coverage == 1.0
    assert result.candidate_generation.exact_scoring_completed is True
    assert result.candidate_generation.qualifying_windows == 2
    assert not result.verified_matches
    assert len(result.unresolved_system) == 1
    assert result.unresolved_system[0].retrieval_lanes == [
        "frame:atom:a1",
        "frame:whole_query",
    ]
    assert result.unresolved_system[0].constraints[0].state == "undetermined"
    assert result.archive_conclusion == ArchiveConclusion.SEARCH_INCOMPLETE
    assert "SYSTEM COULD NOT ASSESS" in result.headline()
    assert all(
        outcome.t0 >= 2 and outcome.t1 <= 6
        for outcome in scoped.unresolved_system
    )


def test_media_route_only_serves_archive_resolved_ids(tmp_path: Path) -> None:
    database, store_path, source = _build_archive(tmp_path)
    pipeline = LocalRetrievalPipeline(
        database=database,
        frame_store=store_path,
        encoder=DeterministicTextEncoder(),
        thresholds={
            "whole_query_frame": 0.5,
            "candidate_anchor_frame": 0.5,
            "rare_attribute_frame": 0.5,
        },
        window_scales=(4,),
    )
    try:
        service = RazielService(
            query_handler=pipeline.query,
            media_resolver=pipeline.media_path,
        )
        client = TestClient(create_app(service))
        response = client.get("/media/video-1")
        assert response.status_code == 200
        assert response.content == source.read_bytes()
        assert client.get("/media/not-in-archive").status_code == 404
    finally:
        pipeline.close()


def test_missing_index_ticks_can_never_be_a_clean_no_match(tmp_path: Path) -> None:
    database, store_path, _source = _build_archive(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        UPDATE sample_ticks
        SET status='skipped',error_code='fixture_missing_embedding'
        WHERE frame_id=3
        """
    )
    connection.commit()
    connection.close()
    pipeline = LocalRetrievalPipeline(
        database=database,
        frame_store=store_path,
        encoder=NegativeTextEncoder(),
        thresholds={
            "whole_query_frame": 1.0,
            "candidate_anchor_frame": 1.0,
            "rare_attribute_frame": 1.0,
        },
        window_scales=(4,),
    )
    try:
        result = pipeline.query({"text": "a red object"})
    finally:
        pipeline.close()
    assert result.indexing.scored_coverage == 2 / 3
    assert result.candidate_generation.exact_scoring_completed is False
    assert result.archive_conclusion == ArchiveConclusion.SEARCH_INCOMPLETE


def test_enabled_graph_lane_unions_active_generation_candidates(tmp_path: Path) -> None:
    database, store_path, _source = _build_archive(tmp_path)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE evidence_nodes (
          node_id TEXT PRIMARY KEY, node_type TEXT, video_id TEXT,
          t0 REAL, t1 REAL, payload_json TEXT, producer_version TEXT
        );
        CREATE TABLE evidence_edges (
          edge_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
          object_node_id TEXT, t0 REAL, t1 REAL, evidence_json TEXT,
          producer_version TEXT
        );
        CREATE TABLE graph_generation_nodes (
          generation_key TEXT, node_id TEXT
        );
        CREATE TABLE graph_generation_edges (
          generation_key TEXT, edge_id TEXT
        );
        CREATE TABLE active_graph_generations (
          video_id TEXT PRIMARY KEY, generation_key TEXT
        );
        CREATE VIEW active_evidence_nodes AS
          SELECT n.* FROM evidence_nodes n
          JOIN graph_generation_nodes m ON m.node_id=n.node_id
          JOIN active_graph_generations a ON a.generation_key=m.generation_key;
        CREATE VIEW active_evidence_edges AS
          SELECT e.* FROM evidence_edges e
          JOIN graph_generation_edges m ON m.edge_id=e.edge_id
          JOIN active_graph_generations a ON a.generation_key=m.generation_key;
        INSERT INTO evidence_nodes VALUES
          ('active-person','detection','video-1',2,3,
           '{"label":"person","camera_id":"gate-01"}','detector-fixture');
        INSERT INTO graph_generation_nodes VALUES ('g-active','active-person');
        INSERT INTO active_graph_generations VALUES ('video-1','g-active');
        """
    )
    connection.commit()
    connection.close()
    pipeline = LocalRetrievalPipeline(
        database=database,
        frame_store=store_path,
        encoder=NegativeTextEncoder(),
        thresholds={
            "whole_query_frame": 1.0,
            "candidate_anchor_frame": 1.0,
            "rare_attribute_frame": 1.0,
        },
        window_scales=(4,),
        graph_enabled=True,
    )
    try:
        result = pipeline.query({"text": "a person", "camera_ids": ["gate-01"]})
    finally:
        pipeline.close()
    assert result.graph_execution.enabled is True
    assert result.graph_execution.node_ids == ["active-person"]
    assert "graph:pattern" in result.candidate_generation.channels_run
    assert len(result.unresolved_system) == 1
    assert result.unresolved_system[0].retrieval_lanes == ["graph:pattern"]
    assert result.unresolved_system[0].graph_node_ids == ["active-person"]


def test_optional_structured_verifier_promotes_only_supported_candidates(
    tmp_path: Path,
) -> None:
    database, store_path, _source = _build_archive(tmp_path)

    def verifier(cluster: object, _plan: object) -> VerificationResult:
        return VerificationResult(
            candidate_id=cluster.cluster_id,
            model_revision="verifier-fixture",
            prompt_schema_version="verify-v1",
            atoms=[
                ConstraintEvidence(
                    constraint_id="a1",
                    state="supported",
                    reason_code="visible_match",
                    evidence_frame_ids=[1],
                    rationale="fixture support",
                )
            ],
        )

    pipeline = LocalRetrievalPipeline(
        database=database,
        frame_store=store_path,
        encoder=DeterministicTextEncoder(),
        thresholds={
            "whole_query_frame": 0.5,
            "candidate_anchor_frame": 0.5,
            "rare_attribute_frame": 0.5,
        },
        window_scales=(4,),
        candidate_verifier=verifier,
    )
    try:
        result = pipeline.query({"text": "a red object"})
    finally:
        pipeline.close()
    assert len(result.verified_matches) == 1
    assert not result.unresolved_system
    assert result.verification.state == "complete"
    assert result.verification.clusters_verified == 1
    assert result.archive_conclusion == ArchiveConclusion.VERIFIED_MATCHES_FOUND


def test_mixed_visual_system_policy_remains_explicitly_unfrozen(
    tmp_path: Path,
) -> None:
    database, store_path, _source = _build_archive(tmp_path)

    def verifier(cluster: object, _plan: object) -> VerificationResult:
        return VerificationResult(
            candidate_id=cluster.cluster_id,
            model_revision="verifier-fixture",
            prompt_schema_version="verify-v1",
            atoms=[
                ConstraintEvidence(
                    constraint_id="a1",
                    state="unobservable",
                    reason_code="occlusion",
                    rationale="fixture occlusion",
                ),
                ConstraintEvidence(
                    constraint_id="a2",
                    state="undetermined",
                    reason_code="model_error",
                    rationale="fixture model error",
                ),
                ConstraintEvidence(
                    constraint_id="a3",
                    state="supported",
                    reason_code="visible_match",
                    evidence_frame_ids=[1],
                    rationale="fixture support",
                ),
            ],
        )

    pipeline = LocalRetrievalPipeline(
        database=database,
        frame_store=store_path,
        encoder=DeterministicTextEncoder(),
        thresholds={
            "whole_query_frame": 0.5,
            "candidate_anchor_frame": 0.5,
            "rare_attribute_frame": 0.5,
        },
        window_scales=(4,),
        candidate_verifier=verifier,
    )
    try:
        with pytest.raises(ValueError, match="pending shared reporting policy"):
            pipeline.query({"text": "person with a black bag"})
    finally:
        pipeline.close()
