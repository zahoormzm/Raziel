"""Optional ASR segment persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


ASR_SCHEMA = """
CREATE TABLE IF NOT EXISTS asr_segments (
    asr_id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    t0 REAL NOT NULL,
    t1 REAL NOT NULL,
    text TEXT NOT NULL,
    language TEXT,
    asr_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asr_video_time ON asr_segments(video_id,t0,t1);
"""


@dataclass(frozen=True)
class ASRSegment:
    t0: float
    t1: float
    text: str
    language: str | None = None


class ASREngine(Protocol):
    @property
    def revision(self) -> str: ...

    def transcribe(self, media_path: str | Path) -> Sequence[ASRSegment]: ...


def run_asr(
    connection: sqlite3.Connection,
    engine: ASREngine,
    *,
    video_id: str,
    media_path: str | Path,
    enabled: bool,
) -> int:
    if not enabled:
        raise RuntimeError("asr capability is disabled")
    connection.executescript(ASR_SCHEMA)
    segments = engine.transcribe(media_path)
    if any(segment.t0 < 0 or segment.t1 < segment.t0 for segment in segments):
        raise ValueError("invalid ASR segment interval")
    with connection:
        connection.executemany(
            """
            INSERT INTO asr_segments(video_id,t0,t1,text,language,asr_version)
            VALUES (?,?,?,?,?,?)
            """,
            [
                (
                    video_id,
                    segment.t0,
                    segment.t1,
                    segment.text,
                    segment.language,
                    engine.revision,
                )
                for segment in segments
            ],
        )
    return len(segments)
