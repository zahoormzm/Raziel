"""Thread-safe, restart-exportable job state for local orchestration."""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobKind(StrEnum):
    INGEST = "ingest"
    QUERY = "query"
    EXPORT = "export"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class JobEvent:
    sequence: int
    stage: str
    message: str
    at: str = field(default_factory=_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobRecord:
    job_id: str
    kind: JobKind
    state: JobState
    request: dict[str, Any]
    created_at: str
    updated_at: str
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[JobEvent] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


class JobRegistry:
    """Small in-process registry.

    It deliberately exposes snapshot/restore so the local service can persist it
    to SQLite later without leaking mutable records to callers.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def create(self, kind: JobKind, request: dict[str, Any]) -> JobRecord:
        now = _now_iso()
        record = JobRecord(
            job_id=uuid.uuid4().hex,
            kind=kind,
            state=JobState.QUEUED,
            request=dict(request),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[record.job_id] = record
        return self.get(record.job_id)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            return self._clone(record)

    def list(self, kind: JobKind | None = None) -> list[JobRecord]:
        with self._lock:
            records = [
                self._clone(record)
                for record in self._jobs.values()
                if kind is None or record.kind == kind
            ]
        return sorted(records, key=lambda item: item.created_at)

    def start(self, job_id: str, stage: str = "started") -> JobRecord:
        return self.update(job_id, state=JobState.RUNNING, stage=stage, progress=0.0)

    def update(
        self,
        job_id: str,
        *,
        state: JobState | None = None,
        stage: str,
        message: str = "",
        progress: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.state in {JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED}:
                raise ValueError("terminal jobs cannot be updated")
            if progress is not None:
                if progress < record.progress or not 0.0 <= progress <= 1.0:
                    raise ValueError("progress must be monotonic and between 0 and 1")
                record.progress = progress
            if state is not None:
                record.state = state
            record.updated_at = _now_iso()
            record.events.append(
                JobEvent(
                    sequence=len(record.events) + 1,
                    stage=stage,
                    message=message,
                    payload=dict(payload or {}),
                )
            )
            return self._clone(record)

    def complete(self, job_id: str, result: dict[str, Any]) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.state not in {JobState.QUEUED, JobState.RUNNING}:
                raise ValueError("only active jobs can complete")
            record.state = JobState.COMPLETE
            record.progress = 1.0
            record.result = dict(result)
            record.updated_at = _now_iso()
            record.events.append(
                JobEvent(
                    sequence=len(record.events) + 1,
                    stage="complete",
                    message="job completed",
                )
            )
            return self._clone(record)

    def fail(self, job_id: str, error: str) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.state in {JobState.COMPLETE, JobState.FAILED, JobState.CANCELLED}:
                raise ValueError("terminal jobs cannot fail again")
            record.state = JobState.FAILED
            record.error = error
            record.updated_at = _now_iso()
            record.events.append(
                JobEvent(
                    sequence=len(record.events) + 1,
                    stage="failed",
                    message=error,
                )
            )
            return self._clone(record)

    def snapshot(self) -> list[dict[str, Any]]:
        return [record.to_wire() for record in self.list()]

    def _require(self, job_id: str) -> JobRecord:
        record = self._jobs.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    @staticmethod
    def _clone(record: JobRecord) -> JobRecord:
        return JobRecord(
            job_id=record.job_id,
            kind=record.kind,
            state=record.state,
            request=dict(record.request),
            created_at=record.created_at,
            updated_at=record.updated_at,
            progress=record.progress,
            result=dict(record.result) if record.result is not None else None,
            error=record.error,
            events=[
                JobEvent(
                    sequence=event.sequence,
                    stage=event.stage,
                    message=event.message,
                    at=event.at,
                    payload=dict(event.payload),
                )
                for event in record.events
            ],
        )
