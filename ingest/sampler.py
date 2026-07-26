"""Expected-tick ledger and PTS-safe frame assignment.

The ledger deliberately uses ``skipped/error_code=pending`` for planned work because
the frozen prototype schema exposes only embedded/decode_failed/skipped states.
Planned, decoded-but-not-embedded, and feature-disabled reasons remain explicit in
``error_code`` and all rows remain in the coverage denominator.
"""

from __future__ import annotations

import bisect
import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from .hash_source import sha256_file
from .motion import MotionSample, frame_difference, motion_ticks
from .probe import VideoProbe, probe_video
from .quality import assess_quality
from .windows import build_windows


SamplingLane = Literal["base", "motion", "refine"]
TickStatus = Literal["embedded", "decode_failed", "skipped"]
T = TypeVar("T")


@dataclass(frozen=True)
class SamplingTick:
    video_id: str
    target_pts: float
    sampling_lane: SamplingLane
    frame_id: int | None = None
    status: TickStatus = "skipped"
    error_code: str | None = "pending"

    def as_contract_dict(self) -> dict[str, Any]:
        return {"schema_version": "1.0.0", **asdict(self)}


@dataclass(frozen=True)
class CoverageReport:
    expected_ticks: int
    embedded_ticks: int
    decode_failed_ticks: int
    skipped_ticks: int
    pending_ticks: int
    scored_coverage: float


@dataclass(frozen=True)
class DecodedAssignment(Generic[T]):
    tick: float
    actual_pts: float | None
    payload: T | None
    error_code: str | None = None


@dataclass(frozen=True)
class IngestResult:
    manifest: Any
    coverage: CoverageReport
    cache_key: str


INGEST_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    camera_id TEXT,
    sha256 TEXT NOT NULL,
    ffprobe_json TEXT NOT NULL,
    recording_start TEXT,
    timebase TEXT,
    duration_s REAL,
    ingested_at TEXT,
    pipeline_version TEXT,
    cache_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS sample_ticks (
    video_id TEXT NOT NULL,
    target_pts REAL NOT NULL,
    sampling_lane TEXT NOT NULL CHECK(sampling_lane IN ('base','motion','refine')),
    frame_id INTEGER,
    status TEXT NOT NULL CHECK(status IN ('embedded','decode_failed','skipped')),
    error_code TEXT,
    PRIMARY KEY(video_id, target_pts, sampling_lane)
);
CREATE TABLE IF NOT EXISTS frames (
    frame_id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    pts_seconds REAL NOT NULL,
    sampling_lane TEXT NOT NULL,
    luminance REAL,
    sharpness REAL,
    motion_score REAL,
    decode_ok INTEGER NOT NULL,
    frame_embedding_row INTEGER,
    thumbnail_path TEXT
);
CREATE TABLE IF NOT EXISTS windows (
    window_id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    scale_s INTEGER NOT NULL,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL
);
-- Collapse duplicate window rows before asserting uniqueness. This schema is
-- applied with executescript on every ExpectedTickLedger and GraphStore
-- construction, so without this DELETE the CREATE UNIQUE INDEX below raises
-- IntegrityError on any archive that was re-ingested under the old code, the
-- script aborts at that statement, and the database becomes unopenable by
-- ingest, graph build and the query pipeline -- bricking exactly the archives
-- the index exists to protect. Keeping MIN(rowid) preserves the first-written
-- window, which is the one INSERT OR IGNORE would have kept had the constraint
-- existed. Any evidence graph built over the duplicates should be regenerated;
-- its window nodes were duplicated for the same reason.
DELETE FROM windows
WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM windows GROUP BY video_id, scale_s, t0, t1
);
-- Without this, `INSERT OR IGNORE INTO windows` has no conflict target to
-- ignore, so re-ingesting a source appended a full duplicate set of windows
-- (19 -> 38 -> 57 rows across three runs). Re-running ingest is the documented
-- resume path, and the duplicates propagated: GraphBuilder emits one window
-- evidence node per row, inflating expected/observed tick counts in the
-- observation-safety check and producing duplicate candidates for one interval.
CREATE UNIQUE INDEX IF NOT EXISTS idx_windows_identity
    ON windows(video_id, scale_s, t0, t1);
CREATE TABLE IF NOT EXISTS ingest_generations (
    cache_key TEXT PRIMARY KEY,
    inputs_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sample_ticks_status
    ON sample_ticks(video_id, sampling_lane, status);
CREATE INDEX IF NOT EXISTS idx_frames_video_pts
    ON frames(video_id, pts_seconds);
CREATE INDEX IF NOT EXISTS idx_windows_video_time
    ON windows(video_id, t0, t1);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expected_ticks(
    duration_s: float,
    *,
    interval_s: float = 1.0,
    start_s: float = 0.0,
    end_s: float | None = None,
) -> list[float]:
    """Return half-open ticks [start, end) using integer microseconds."""
    if duration_s < 0 or start_s < 0:
        raise ValueError("duration and start must be non-negative")
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    stop_s = duration_s if end_s is None else min(duration_s, end_s)
    if stop_s <= start_s:
        return []
    start_us = round(start_s * 1_000_000)
    stop_us = round(stop_s * 1_000_000)
    step_us = max(1, round(interval_s * 1_000_000))
    values: list[float] = []
    tick_us = start_us
    while tick_us < stop_us:
        values.append(tick_us / 1_000_000)
        tick_us += step_us
    return values


def plan_refinement_ticks(
    ledger: "ExpectedTickLedger",
    *,
    video_id: str,
    t0: float,
    t1: float,
    fps: float = 4.0,
) -> int:
    """Declare candidate-only dense refinement work before decoding it."""
    if t0 < 0 or t1 < t0:
        raise ValueError("invalid refinement interval")
    if not 2.0 <= fps <= 4.0:
        raise ValueError("refinement fps must stay within the declared 2-4 fps lane")
    return ledger.plan(
        video_id,
        expected_ticks(t1, interval_s=1.0 / fps, start_s=t0, end_s=t1),
        "refine",
    )


def assign_nearest_pts(
    ticks: Sequence[float],
    decoded_pts: Sequence[float],
    *,
    max_distance_s: float | None = None,
) -> list[DecodedAssignment[float]]:
    """Assign each target to the nearest real PTS; ties choose the earlier PTS."""
    import bisect

    if any(right < left for left, right in zip(decoded_pts, decoded_pts[1:])):
        raise ValueError("decoded PTS regression detected")
    output: list[DecodedAssignment[float]] = []
    for tick in ticks:
        position = bisect.bisect_left(decoded_pts, tick)
        choices = []
        if position:
            choices.append(decoded_pts[position - 1])
        if position < len(decoded_pts):
            choices.append(decoded_pts[position])
        if not choices:
            output.append(
                DecodedAssignment(tick=tick, actual_pts=None, payload=None, error_code="no_decodable_frame")
            )
            continue
        nearest = min(choices, key=lambda pts: (abs(pts - tick), pts))
        if max_distance_s is not None and abs(nearest - tick) > max_distance_s:
            output.append(
                DecodedAssignment(
                    tick=tick,
                    actual_pts=None,
                    payload=None,
                    error_code="nearest_frame_too_far",
                )
            )
        else:
            output.append(DecodedAssignment(tick=tick, actual_pts=nearest, payload=nearest))
    return output


def assign_decoded_frames(
    ticks: Sequence[float],
    frames: Iterable[tuple[float, T]],
    *,
    max_distance_s: float | None = None,
) -> Iterator[DecodedAssignment[T]]:
    """Stream nearest-frame assignment without deriving time from frame index."""
    targets = iter(ticks)
    target = next(targets, None)
    previous: tuple[float, T] | None = None
    for current in frames:
        if previous is not None and current[0] < previous[0]:
            raise ValueError(
                f"decoded PTS regression detected: {current[0]} < {previous[0]}"
            )
        if previous is None:
            previous = current
            continue
        midpoint = (previous[0] + current[0]) / 2.0
        while target is not None and target <= midpoint:
            chosen = previous if target <= midpoint else current
            distance = abs(chosen[0] - target)
            if max_distance_s is not None and distance > max_distance_s:
                yield DecodedAssignment(
                    tick=target,
                    actual_pts=None,
                    payload=None,
                    error_code="nearest_frame_too_far",
                )
            else:
                yield DecodedAssignment(
                    tick=target, actual_pts=chosen[0], payload=chosen[1]
                )
            target = next(targets, None)
        previous = current
    while target is not None:
        if previous is None:
            yield DecodedAssignment(
                tick=target,
                actual_pts=None,
                payload=None,
                error_code="no_decodable_frame",
            )
        elif max_distance_s is not None and abs(previous[0] - target) > max_distance_s:
            yield DecodedAssignment(
                tick=target,
                actual_pts=None,
                payload=None,
                error_code="nearest_frame_too_far",
            )
        else:
            yield DecodedAssignment(
                tick=target, actual_pts=previous[0], payload=previous[1]
            )
        target = next(targets, None)


class ExpectedTickLedger:
    """Restart-safe SQLite ledger for declared sampling work."""

    def __init__(self, database: str | Path | sqlite3.Connection):
        self._owns_connection = not isinstance(database, sqlite3.Connection)
        self.connection = (
            sqlite3.connect(str(database))
            if self._owns_connection
            else database
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(INGEST_SCHEMA)

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> "ExpectedTickLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def register_video(
        self,
        *,
        video_id: str,
        path: str | Path,
        sha256: str,
        probe: VideoProbe,
        camera_id: str | None,
        pipeline_version: str,
        cache_key: str,
        artifact_inputs: dict[str, Any] | None = None,
    ) -> None:
        values = (
            video_id,
            str(Path(path).resolve()),
            camera_id,
            sha256,
            probe.raw_json(),
            probe.recording_start,
            probe.time_base,
            probe.duration_s,
            utc_now(),
            pipeline_version,
            cache_key,
        )
        with self.connection:
            existing = self.connection.execute(
                "SELECT sha256, cache_key FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
            if existing and (
                existing["sha256"] != sha256 or existing["cache_key"] != cache_key
            ):
                raise ValueError(
                    f"video_id {video_id!r} already refers to a different source/config"
                )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO videos (
                    video_id,path,camera_id,sha256,ffprobe_json,recording_start,
                    timebase,duration_s,ingested_at,pipeline_version,cache_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            if artifact_inputs is not None:
                serialized = json.dumps(
                    artifact_inputs, sort_keys=True, separators=(",", ":")
                )
                self.connection.execute(
                    """
                    INSERT INTO ingest_generations(cache_key,inputs_json)
                    VALUES (?,?) ON CONFLICT(cache_key) DO NOTHING
                    """,
                    (cache_key, serialized),
                )
                actual = self.connection.execute(
                    "SELECT inputs_json FROM ingest_generations WHERE cache_key=?",
                    (cache_key,),
                ).fetchone()[0]
                if actual != serialized:
                    raise ValueError("ingest generation cache-key collision")

    def plan(
        self,
        video_id: str,
        ticks: Iterable[float],
        lane: SamplingLane = "base",
    ) -> int:
        rows = [
            (video_id, float(tick), lane, None, "skipped", "pending")
            for tick in ticks
        ]
        before = self.connection.total_changes
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO sample_ticks(
                    video_id,target_pts,sampling_lane,frame_id,status,error_code
                ) VALUES (?,?,?,?,?,?)
                """,
                rows,
            )
        return self.connection.total_changes - before

    def pending_ticks(
        self, video_id: str, lane: SamplingLane = "base"
    ) -> list[float]:
        return [
            float(row[0])
            for row in self.connection.execute(
                """
                SELECT target_pts FROM sample_ticks
                WHERE video_id=? AND sampling_lane=?
                  AND status='skipped' AND error_code='pending'
                ORDER BY target_pts
                """,
                (video_id, lane),
            )
        ]

    def record_decode_failure(
        self,
        video_id: str,
        target_pts: float,
        lane: SamplingLane,
        error_code: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE sample_ticks SET frame_id=NULL,status='decode_failed',error_code=?
                WHERE video_id=? AND target_pts=? AND sampling_lane=?
                """,
                (error_code, video_id, target_pts, lane),
            )

    def record_decoded_frame(
        self,
        *,
        video_id: str,
        target_pts: float,
        actual_pts: float,
        lane: SamplingLane,
        luminance: float | None = None,
        sharpness: float | None = None,
        motion_score: float | None = None,
        thumbnail_path: str | None = None,
    ) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO frames(
                    video_id,pts_seconds,sampling_lane,luminance,sharpness,
                    motion_score,decode_ok,frame_embedding_row,thumbnail_path
                ) VALUES (?,?,?,?,?,?,1,NULL,?)
                """,
                (
                    video_id,
                    actual_pts,
                    lane,
                    luminance,
                    sharpness,
                    motion_score,
                    thumbnail_path,
                ),
            )
            frame_id = int(cursor.lastrowid)
            updated = self.connection.execute(
                """
                UPDATE sample_ticks
                SET frame_id=?,status='skipped',error_code='embedding_pending'
                WHERE video_id=? AND target_pts=? AND sampling_lane=?
                """,
                (frame_id, video_id, target_pts, lane),
            )
            if updated.rowcount != 1:
                raise KeyError("tick must be planned before decode")
        return frame_id

    def record_embedding(
        self, *, frame_id: int, embedding_row: int
    ) -> None:
        with self.connection:
            updated = self.connection.execute(
                """
                UPDATE frames SET frame_embedding_row=? WHERE frame_id=?
                  AND frame_embedding_row IS NULL
                """,
                (embedding_row, frame_id),
            )
            if updated.rowcount != 1:
                existing = self.connection.execute(
                    "SELECT frame_embedding_row FROM frames WHERE frame_id=?", (frame_id,)
                ).fetchone()
                if existing is None or existing[0] != embedding_row:
                    raise ValueError("frame embedding row is missing or already differs")
            self.connection.execute(
                """
                UPDATE sample_ticks SET status='embedded',error_code=NULL
                WHERE frame_id=?
                """,
                (frame_id,),
            )

    def coverage(
        self,
        video_id: str | None = None,
        lane: SamplingLane | None = None,
    ) -> CoverageReport:
        clauses: list[str] = []
        params: list[Any] = []
        if video_id is not None:
            clauses.append("video_id=?")
            params.append(video_id)
        if lane is not None:
            clauses.append("sampling_lane=?")
            params.append(lane)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        row = self.connection.execute(
            f"""
            SELECT COUNT(*) AS expected,
                   SUM(status='embedded') AS embedded,
                   SUM(status='decode_failed') AS failed,
                   SUM(status='skipped') AS skipped,
                   SUM(status='skipped' AND error_code='pending') AS pending
            FROM sample_ticks {where}
            """,
            params,
        ).fetchone()
        expected = int(row["expected"] or 0)
        embedded = int(row["embedded"] or 0)
        return CoverageReport(
            expected_ticks=expected,
            embedded_ticks=embedded,
            decode_failed_ticks=int(row["failed"] or 0),
            skipped_ticks=int(row["skipped"] or 0),
            pending_ticks=int(row["pending"] or 0),
            scored_coverage=(embedded / expected if expected else 0.0),
        )

    def video_manifest(self, video_id: str, *, sampling_policy_version: str) -> Any:
        """Build the shared boundary contract from canonical SQLite state."""
        from packages.contracts.video_manifest import FrameTick, VideoManifest

        video = self.connection.execute(
            "SELECT * FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()
        if video is None:
            raise KeyError(video_id)
        rows = self.connection.execute(
            """
            SELECT t.video_id,t.target_pts,t.sampling_lane,t.status,t.frame_id,
                   f.pts_seconds AS actual_pts,t.error_code
            FROM sample_ticks AS t
            LEFT JOIN frames AS f ON f.frame_id=t.frame_id
            WHERE t.video_id=?
            ORDER BY t.sampling_lane,t.target_pts
            """,
            (video_id,),
        ).fetchall()
        ticks = [
            FrameTick(
                video_id=row["video_id"],
                target_pts=row["target_pts"],
                sampling_lane=row["sampling_lane"],
                status=row["status"],
                frame_id=row["frame_id"],
                actual_pts=row["actual_pts"],
                error_code=row["error_code"],
            )
            for row in rows
        ]
        coverage = self.coverage(video_id)
        generation = self.connection.execute(
            "SELECT inputs_json FROM ingest_generations WHERE cache_key=?",
            (video["cache_key"],),
        ).fetchone()
        return VideoManifest(
            video_id=video["video_id"],
            path=video["path"],
            camera_id=video["camera_id"],
            sha256=video["sha256"],
            ffprobe_json=json.loads(video["ffprobe_json"]),
            recording_start=video["recording_start"],
            timebase=video["timebase"],
            duration_s=video["duration_s"],
            ingested_at=video["ingested_at"],
            pipeline_version=video["pipeline_version"],
            cache_key=video["cache_key"],
            sampling_policy_version=sampling_policy_version,
            expected_ticks=coverage.expected_ticks,
            embedded_ticks=coverage.embedded_ticks,
            decode_failed_ticks=coverage.decode_failed_ticks,
            skipped_ticks=coverage.skipped_ticks,
            ticks=ticks,
            artifact_inputs=(
                json.loads(generation["inputs_json"]) if generation is not None else None
            ),
        )


def _decode_pyav(path: Path) -> Iterator[tuple[float, Any]]:
    try:
        import av  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyAV is required for frame decode; install it or run metadata-only ingestion"
        ) from exc
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise RuntimeError("source contains no video stream")
        for frame in container.decode(stream):
            if frame.pts is None or frame.time_base is None:
                continue
            pts_seconds = float(frame.pts * frame.time_base)
            yield pts_seconds, frame.to_ndarray(format="rgb24")


def _motion_samples_pyav(path: Path, *, stride: int = 4) -> Iterator[MotionSample]:
    previous = None
    for pts_seconds, frame in _decode_pyav(path):
        # Striding preserves a deterministic low-resolution activity signal.
        low_resolution = frame[::stride, ::stride]
        if previous is not None:
            yield MotionSample(
                pts_seconds=pts_seconds,
                score=frame_difference(previous, low_resolution),
            )
        previous = low_resolution


def detected_decoder_version() -> str:
    """Return actual PyAV/linked-FFmpeg versions without importing at module load."""
    try:
        import av  # type: ignore
    except ImportError:
        return "pyav-unavailable"
    libraries = ",".join(
        f"{name}={'.'.join(str(part) for part in version)}"
        for name, version in sorted(av.library_versions.items())
    )
    return f"pyav={av.__version__};{libraries}"


def _decode_lane(
    ledger: ExpectedTickLedger,
    *,
    path: Path,
    video_id: str,
    lane: SamplingLane,
    max_tick_distance_s: float | None,
    motion_scores: Sequence[MotionSample] = (),
) -> None:
    pending = ledger.pending_ticks(video_id, lane)
    assignments = assign_decoded_frames(
        pending,
        _decode_pyav(path),
        max_distance_s=max_tick_distance_s,
    )
    motion_pts = [sample.pts_seconds for sample in motion_scores]
    motion_values = [sample.score for sample in motion_scores]
    for assignment in assignments:
        if assignment.payload is None or assignment.actual_pts is None:
            ledger.record_decode_failure(
                video_id,
                assignment.tick,
                lane,
                assignment.error_code or "decode_failed",
            )
            continue
        lum, sharp = assess_quality(assignment.payload)
        score = None
        if motion_pts:
            position = bisect.bisect_left(motion_pts, assignment.actual_pts)
            candidates = []
            if position:
                candidates.append(position - 1)
            if position < len(motion_pts):
                candidates.append(position)
            nearest_index = min(
                candidates,
                key=lambda index: (
                    abs(motion_pts[index] - assignment.actual_pts),
                    motion_pts[index],
                ),
            )
            score = motion_values[nearest_index]
        ledger.record_decoded_frame(
            video_id=video_id,
            target_pts=assignment.tick,
            actual_pts=assignment.actual_pts,
            lane=lane,
            luminance=lum,
            sharpness=sharp,
            motion_score=score,
        )


def build_ingest_cache_key(
    source_hash: str,
    sampling_config: dict[str, Any],
    decoder_version: str,
    preprocessing_version: str,
    embedding_model_revision: str,
) -> str:
    import hashlib

    payload = {
        "source_hash": source_hash,
        "sampling_config": sampling_config,
        "decoder_version": decoder_version,
        "preprocessing_version": preprocessing_version,
        "embedding_model_revision": embedding_model_revision,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def ingest_video(
    source: str | Path,
    database: str | Path,
    *,
    camera_id: str | None = None,
    video_id: str | None = None,
    interval_s: float = 1.0,
    pipeline_version: str = "ingest-v1",
    decoder_version: str | None = None,
    preprocessing_version: str = "rgb24-quality-v1",
    embedding_model_revision: str = "not-indexed",
    ffprobe_binary: str = "ffprobe",
    decode: bool = True,
    max_tick_distance_s: float | None = None,
    motion_enabled: bool = False,
    motion_threshold: float = 0.12,
    motion_dense_fps: float = 4.0,
    motion_padding_s: float = 1.0,
) -> IngestResult:
    if motion_enabled and not 0 < motion_dense_fps <= 4.0:
        raise ValueError("motion densification must be within (0, 4] fps")
    path = Path(source).resolve()
    resolved_decoder_version = decoder_version or detected_decoder_version()
    source_hash = sha256_file(path)
    probe = probe_video(path, ffprobe_binary=ffprobe_binary)
    if probe.duration_s is None:
        raise ValueError("source duration is unavailable; explicit duration is required")
    resolved_video_id = video_id or f"video-{source_hash[:20]}"
    sampling_config = {
        "base_interval_s": interval_s,
        "motion_enabled": motion_enabled,
        "motion_threshold": motion_threshold if motion_enabled else None,
        "motion_dense_fps": motion_dense_fps if motion_enabled else None,
        "motion_padding_s": motion_padding_s if motion_enabled else None,
    }
    cache_key = build_ingest_cache_key(
        source_hash,
        sampling_config,
        resolved_decoder_version,
        preprocessing_version,
        embedding_model_revision,
    )
    artifact_inputs = {
        "source_hash": source_hash,
        "sampling_config": sampling_config,
        "decoder_version": resolved_decoder_version,
        "preprocessing_version": preprocessing_version,
        "embedding_model_revision": embedding_model_revision,
    }
    with ExpectedTickLedger(database) as ledger:
        ledger.register_video(
            video_id=resolved_video_id,
            path=path,
            sha256=source_hash,
            probe=probe,
            camera_id=camera_id,
            pipeline_version=pipeline_version,
            cache_key=cache_key,
            artifact_inputs=artifact_inputs,
        )
        ticks = expected_ticks(probe.duration_s, interval_s=interval_s)
        ledger.plan(resolved_video_id, ticks, "base")
        with ledger.connection:
            ledger.connection.executemany(
                """
                INSERT OR IGNORE INTO windows(video_id,scale_s,t0,t1)
                VALUES (?,?,?,?)
                """,
                [
                    (resolved_video_id, window.scale_s, window.t0, window.t1)
                    for window in build_windows(probe.duration_s)
                ],
            )
        if decode:
            motion_samples: list[MotionSample] = []
            try:
                if motion_enabled:
                    motion_samples = list(_motion_samples_pyav(path))
                    ledger.plan(
                        resolved_video_id,
                        motion_ticks(
                            motion_samples,
                            threshold=motion_threshold,
                            dense_fps=motion_dense_fps,
                            padding_s=motion_padding_s,
                            duration_s=probe.duration_s,
                        ),
                        "motion",
                    )
                _decode_lane(
                    ledger,
                    path=path,
                    video_id=resolved_video_id,
                    lane="base",
                    max_tick_distance_s=max_tick_distance_s,
                    motion_scores=motion_samples,
                )
                if motion_enabled:
                    _decode_lane(
                        ledger,
                        path=path,
                        video_id=resolved_video_id,
                        lane="motion",
                        max_tick_distance_s=max_tick_distance_s,
                        motion_scores=motion_samples,
                    )
            except Exception as exc:
                # Preserve the complete expected denominator and a stable error class.
                error = f"decoder_{type(exc).__name__.lower()}"
                for lane in ("base", "motion"):
                    for tick in ledger.pending_ticks(resolved_video_id, lane):
                        ledger.record_decode_failure(
                            resolved_video_id, tick, lane, error
                        )
                raise
        manifest = ledger.video_manifest(
            resolved_video_id, sampling_policy_version=pipeline_version
        )
        return IngestResult(
            manifest=manifest,
            coverage=ledger.coverage(resolved_video_id),
            cache_key=cache_key,
        )
