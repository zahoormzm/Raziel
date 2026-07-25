from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from ingest.hash_source import sha256_file
from ingest.motion import MotionSample, motion_ticks
from ingest.probe import parse_probe
from ingest.sampler import (
    ExpectedTickLedger,
    assign_decoded_frames,
    assign_nearest_pts,
    build_ingest_cache_key,
    expected_ticks,
)
from ingest.windows import Window, aggregate_window_scores, build_windows


def _probe():
    return parse_probe(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "time_base": "1/90000",
                    "duration": "3.0",
                    "tags": {"creation_time": "2026-01-01T00:00:00Z"},
                }
            ],
            "format": {"duration": "3.0"},
        }
    )


def test_streaming_hash_matches_sha256(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc" * 10000)
    assert sha256_file(source) == hashlib.sha256(source.read_bytes()).hexdigest()


def test_ingest_cache_key_is_canonical() -> None:
    common = ("a" * 64,)
    left = build_ingest_cache_key(
        *common, {"interval": 1, "motion": False}, "decoder", "prep", "model"
    )
    right = build_ingest_cache_key(
        *common, {"motion": False, "interval": 1}, "decoder", "prep", "model"
    )
    assert left == right


def test_expected_tick_half_open_and_pts_assignment() -> None:
    assert expected_ticks(3.0) == [0.0, 1.0, 2.0]
    assignments = assign_nearest_pts(
        [0.0, 1.0, 2.0], [0.04, 0.9, 1.1, 2.2]
    )
    assert [item.actual_pts for item in assignments] == [0.04, 0.9, 2.2]
    # Equidistant frames deterministically prefer the earlier real PTS.
    assert assign_nearest_pts([1.0], [0.9, 1.1])[0].actual_pts == 0.9


def test_streaming_assignment_rejects_timestamp_regression() -> None:
    with pytest.raises(ValueError, match="regression"):
        list(assign_decoded_frames([0.0, 1.0], [(0.1, "a"), (0.0, "b")]))


def test_restart_idempotence_and_failed_ticks_stay_in_denominator(
    tmp_path: Path,
) -> None:
    database = tmp_path / "archive.sqlite"
    with ExpectedTickLedger(database) as ledger:
        ledger.register_video(
            video_id="v1",
            path=tmp_path / "v.mp4",
            sha256="a" * 64,
            probe=_probe(),
            camera_id="cam-1",
            pipeline_version="ingest-v1",
            cache_key="cache-1",
        )
        assert ledger.plan("v1", [0.0, 1.0, 2.0]) == 3
        assert ledger.plan("v1", [0.0, 1.0, 2.0]) == 0
        frame = ledger.record_decoded_frame(
            video_id="v1",
            target_pts=0.0,
            actual_pts=0.04,
            lane="base",
        )
        ledger.record_embedding(frame_id=frame, embedding_row=0)
        ledger.record_decode_failure("v1", 1.0, "base", "forced_failure")
        coverage = ledger.coverage("v1")
        assert coverage.expected_ticks == 3
        assert coverage.embedded_ticks == 1
        assert coverage.decode_failed_ticks == 1
        assert coverage.skipped_ticks == 1
        assert coverage.scored_coverage == pytest.approx(1 / 3)
        manifest = ledger.video_manifest(
            "v1", sampling_policy_version="sampling-v1"
        )
        assert manifest.expected_ticks == 3
        assert manifest.ticks[0].actual_pts == pytest.approx(0.04)
        assert manifest.to_wire()["schema_version"] == "1.0.0"


def test_one_hour_ledger_hand_count_is_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "hour.sqlite"
    ticks = expected_ticks(3600.0)
    assert len(ticks) == 3600
    with ExpectedTickLedger(database) as ledger:
        ledger.register_video(
            video_id="hour",
            path="hour.mp4",
            sha256="b" * 64,
            probe=parse_probe(
                {
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "time_base": "1/1000",
                            "duration": "3600",
                        }
                    ]
                }
            ),
            camera_id=None,
            pipeline_version="v1",
            cache_key="hour-cache",
        )
        ledger.plan("hour", ticks)
        ledger.plan("hour", ticks)
        assert ledger.coverage("hour").expected_ticks == 3600


def test_motion_densification_and_multiscale_windows() -> None:
    ticks = motion_ticks(
        [MotionSample(2.0, 0.8), MotionSample(2.5, 0.9)],
        threshold=0.5,
        dense_fps=4,
        padding_s=0.5,
        duration_s=4.0,
    )
    assert ticks == [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]
    windows = build_windows(31.0)
    assert {window.scale_s for window in windows} == {4, 12, 30}
    assert aggregate_window_scores(
        [0.0, 1.0, 2.0], [0.1, 0.8, 0.4], [Window(2, 0.0, 2.0)]
    ) == [0.8]
