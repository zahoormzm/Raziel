"""Optional OCR lane with synchronized external-content FTS."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


OCR_SCHEMA = """
CREATE TABLE IF NOT EXISTS ocr_hits (
    ocr_id INTEGER PRIMARY KEY,
    frame_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    bbox_json TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
    text, content='ocr_hits', content_rowid='ocr_id'
);
CREATE TRIGGER IF NOT EXISTS ocr_hits_ai AFTER INSERT ON ocr_hits BEGIN
    INSERT INTO ocr_fts(rowid,text) VALUES (new.ocr_id,new.text);
END;
CREATE TRIGGER IF NOT EXISTS ocr_hits_ad AFTER DELETE ON ocr_hits BEGIN
    INSERT INTO ocr_fts(ocr_fts,rowid,text)
    VALUES('delete',old.ocr_id,old.text);
END;
CREATE TRIGGER IF NOT EXISTS ocr_hits_au AFTER UPDATE ON ocr_hits BEGIN
    INSERT INTO ocr_fts(ocr_fts,rowid,text)
    VALUES('delete',old.ocr_id,old.text);
    INSERT INTO ocr_fts(rowid,text) VALUES (new.ocr_id,new.text);
END;
"""


@dataclass(frozen=True)
class OCRHit:
    frame_id: int
    text: str
    confidence: float | None = None
    bbox_json: str | None = None


class OCREngine(Protocol):
    def recognize(self, frame_ids: Sequence[int], images: Sequence[Any]) -> Sequence[OCRHit]: ...


def run_ocr(
    connection: sqlite3.Connection,
    engine: OCREngine,
    frame_ids: Sequence[int],
    images: Sequence[Any],
    *,
    enabled: bool,
) -> int:
    if not enabled:
        raise RuntimeError("ocr capability is disabled")
    connection.executescript(OCR_SCHEMA)
    hits = engine.recognize(frame_ids, images)
    allowed = set(frame_ids)
    if any(hit.frame_id not in allowed for hit in hits):
        raise ValueError("OCR engine returned a frame outside the request")
    with connection:
        connection.executemany(
            """
            INSERT INTO ocr_hits(frame_id,text,confidence,bbox_json)
            VALUES (?,?,?,?)
            """,
            [(hit.frame_id, hit.text, hit.confidence, hit.bbox_json) for hit in hits],
        )
    return len(hits)


def search_ocr(connection: sqlite3.Connection, quoted_query: str) -> list[sqlite3.Row]:
    """Search extracted text as data; callers must never execute it as instructions."""
    return list(
        connection.execute(
            """
            SELECT h.* FROM ocr_fts AS f
            JOIN ocr_hits AS h ON h.ocr_id=f.rowid
            WHERE ocr_fts MATCH ?
            """,
            (quoted_query,),
        )
    )
