from __future__ import annotations

import json
from pathlib import Path

import pytest

from evidence.graph_build import (
    GraphBuilder,
    ObservationSafetyFields,
)
from evidence.graph_store import GraphStore
from evidence.provenance import canonical_hash
from index.detect import (
    Detection,
    finalize_detection_generation,
    run_detection_block,
)
from index.stores import ResumableBlockState
from index.tracklets import (
    DetectionObservation,
    associate_tracklets,
    persist_tracklets,
)
from ingest.probe import parse_probe
from ingest.sampler import ExpectedTickLedger


def _observation(
    identifier: int,
    *,
    video: str = "v1",
    camera: str = "cam-a",
    pts: float,
    bbox=(0.0, 0.0, 10.0, 10.0),
) -> DetectionObservation:
    return DetectionObservation(
        detection_id=identifier,
        frame_id=identifier,
        video_id=video,
        camera_id=camera,
        pts_seconds=pts,
        label="person",
        bbox=bbox,
        confidence=0.9,
    )


def test_tracklets_never_cross_camera_or_video_session() -> None:
    tracklets = associate_tracklets(
        [
            _observation(1, pts=0.0),
            _observation(2, pts=0.5),
            _observation(3, camera="cam-b", pts=0.6),
            _observation(4, video="v2", pts=0.7),
        ]
    )
    assert sorted(len(item.detections) for item in tracklets) == [1, 1, 2]
    assert all(
        all(
            detection.video_id == item.video_id
            and detection.camera_id == item.camera_id
            for detection in item.detections
        )
        for item in tracklets
    )


def _seed_graph(store: GraphStore) -> None:
    with ExpectedTickLedger(store.connection) as ledger:
        ledger.register_video(
            video_id="v1",
            path="v1.mp4",
            sha256="1" * 64,
            probe=parse_probe(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "index": 0,
                            "time_base": "1/1000",
                            "duration": "3",
                        }
                    ]
                }
            ),
            camera_id="cam-a",
            pipeline_version="ingest-v1",
            cache_key="cache-v1",
        )
        ledger.plan("v1", [0.0, 1.0])
        frame_ids = [
            ledger.record_decoded_frame(
                video_id="v1",
                target_pts=float(tick),
                actual_pts=float(tick) + 0.01,
                lane="base",
            )
            for tick in (0, 1)
        ]
        with ledger.connection:
            ledger.connection.execute(
                "INSERT INTO windows(video_id,scale_s,t0,t1) VALUES ('v1',4,0,3)"
            )
            for frame_id in frame_ids:
                ledger.connection.execute(
                    """
                    INSERT INTO detections(
                        frame_id,label,bbox_json,confidence,detector_version
                    ) VALUES (?,?,?,?,?)
                    """,
                    (frame_id, "person", "[0,0,10,10]", 0.9, "det-v1"),
                )
        detection_rows = list(
            ledger.connection.execute(
                """
                SELECT d.detection_id,d.frame_id,f.video_id,v.camera_id,
                       f.pts_seconds,d.label,d.bbox_json,d.confidence
                FROM detections d
                JOIN frames f ON f.frame_id=d.frame_id
                JOIN videos v ON v.video_id=f.video_id
                ORDER BY d.detection_id
                """
            )
        )
        observations = [
            DetectionObservation(
                detection_id=row[0],
                frame_id=row[1],
                video_id=row[2],
                camera_id=row[3],
                pts_seconds=row[4],
                label=row[5],
                bbox=tuple(json.loads(row[6])),
                confidence=row[7],
            )
            for row in detection_rows
        ]
        tracklets = associate_tracklets(observations, min_iou=0.1, max_gap_s=2)
        persist_tracklets(
            ledger.connection, tracklets, tracker_version="tracker-v1"
        )


def test_detection_tracklet_graph_round_trip_and_provenance(tmp_path: Path) -> None:
    database = tmp_path / "graph.sqlite"
    with GraphStore(database) as store:
        _seed_graph(store)
        builder = GraphBuilder(
            store,
            producer_version="graph-v1",
            sampling_policy_version="sampling-v1",
            detector_version="det-v1",
            tracker_version="tracker-v1",
            thresholds={"iou": 0.1},
        )
        stats = builder.build_video("v1")
        assert stats.nodes_written >= 6
        assert stats.edges_written >= 4
        tracklet = store.connection.execute(
            """
            SELECT payload_json FROM evidence_nodes
            WHERE node_type='tracklet'
            """
        ).fetchone()
        payload = json.loads(tracklet[0])
        assert payload["anonymous_local_continuity_only"] is True
        assert payload["provenance"]["source_sha256"] == "1" * 64
        edge = store.connection.execute(
            """
            SELECT evidence_json FROM evidence_edges
            WHERE predicate='belongs_to_track'
            """
        ).fetchone()
        evidence = json.loads(edge[0])
        assert evidence["frame_ids"]
        assert evidence["relationship_basis"] == "tracker association"
        window_payload = json.loads(
            store.connection.execute(
                """
                SELECT payload_json FROM active_evidence_nodes
                WHERE node_type='window'
                """
            ).fetchone()[0]
        )
        assert window_payload["expected_ticks"] == 2
        assert window_payload["observed_ticks"] == 0
        assert window_payload["assessable_ticks"] == 0
        assert window_payload["occlusion_assessed"] is False
        assert window_payload["assessable"] is False
        episode_id = builder.put_episode(
            video_id="v1",
            episode_id="assessed-gate-interval",
            t0=0.0,
            t1=2.0,
            payload={"frame_ids": [1, 2], "label": "gate observation"},
            observation_safety=ObservationSafetyFields(
                predicate_scope="g-visible-none-gate",
                expected_ticks=2,
                observed_ticks=2,
                assessable_ticks=2,
                occluded_ticks=0,
                low_light_ticks=0,
                region_assessable=True,
                coverage_complete=True,
            ),
        )
        episode_row = store.connection.execute(
            """
            SELECT payload_json FROM active_evidence_nodes WHERE node_id=?
            """,
            (episode_id,),
        ).fetchone()
        assert episode_row is not None
        episode_payload = json.loads(episode_row[0])
        assert episode_payload["predicate_scope"] == "g-visible-none-gate"
        assert episode_payload["assessable"] is True
        assert episode_payload["occlusion_assessed"] is True
        with pytest.raises(ValueError, match="ObservationSafetyFields"):
            builder.put_episode(
                video_id="v1",
                episode_id="unsafe-raw-fields",
                t0=0.0,
                t1=2.0,
                payload={"expected_ticks": 2, "observed_ticks": 2},
            )
        resumed = builder.build_video("v1")
        assert resumed.resumed_complete is True
        next_builder = GraphBuilder(
            store,
            producer_version="graph-v2",
            sampling_policy_version="sampling-v1",
            detector_version="det-v2",
            tracker_version="tracker-v1",
            thresholds={"iou": 0.1},
        )
        next_stats = next_builder.build_video("v1")
        assert next_stats.generation_key != stats.generation_key
        active_count = store.connection.execute(
            "SELECT COUNT(*) FROM active_evidence_nodes WHERE video_id='v1'"
        ).fetchone()[0]
        all_count = store.connection.execute(
            "SELECT COUNT(*) FROM evidence_nodes WHERE video_id='v1'"
        ).fetchone()[0]
        assert active_count == next_stats.nodes_written
        assert all_count > active_count


def test_provenance_canonical_hash_is_order_independent() -> None:
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_detection_generation_must_finalize_before_tracklets(
    tmp_path: Path,
) -> None:
    class FakeDetector:
        revision = "det-fake"

        def detect(self, frame_ids, images):
            return [
                Detection(frame_id, "person", (0, 0, 10, 10), 0.9)
                for frame_id in frame_ids
            ]

    database = tmp_path / "generation.sqlite"
    with GraphStore(database) as store:
        with ExpectedTickLedger(store.connection) as ledger:
            ledger.register_video(
                video_id="v",
                path="v.mp4",
                sha256="f" * 64,
                probe=parse_probe(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "index": 0,
                                "time_base": "1/1000",
                                "duration": "2",
                            }
                        ]
                    }
                ),
                camera_id="cam",
                pipeline_version="v1",
                cache_key="generation-cache",
            )
            ledger.plan("v", [0.0, 1.0])
            frame_ids = [
                ledger.record_decoded_frame(
                    video_id="v",
                    target_pts=tick,
                    actual_pts=tick,
                    lane="base",
                )
                for tick in (0.0, 1.0)
            ]
        checkpoint = ResumableBlockState(
            tmp_path / "detect.json", generation_key="det-generation"
        )
        assert (
            run_detection_block(
                connection=store.connection,
                detector=FakeDetector(),
                frame_ids=frame_ids,
                images=[object(), object()],
                checkpoint=checkpoint,
                block_id="block-1",
                enabled=True,
            )
            == 2
        )
        from index.tracklets import build_tracklets

        with pytest.raises(RuntimeError, match="finalized"):
            build_tracklets(
                store.connection,
                "v",
                tracker_version="tracker",
                enabled=True,
            )
        finalize_detection_generation(
            store.connection,
            video_id="v",
            generation_key="det-generation",
            expected_block_ids=["block-1"],
        )
        identifiers = build_tracklets(
            store.connection,
            "v",
            tracker_version="tracker",
            min_iou=0.1,
            max_gap_s=2,
            enabled=True,
        )
        assert len(identifiers) == 1
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM active_tracklets WHERE video_id='v'"
            ).fetchone()[0]
            == 1
        )
