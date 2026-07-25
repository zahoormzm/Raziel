"""Exclusive GPU leases and a durable local job registry."""

from __future__ import annotations

from contextlib import AbstractContextManager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping


LEASE_MODES = frozenset({"index", "train", "serve"})
TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled"})


class LeaseBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseRecord:
    mode: str
    owner: str
    pid: int
    acquired_at: str


class GPULease(AbstractContextManager["GPULease"]):
    def __init__(self, path: str | Path, *, mode: str, owner: str) -> None:
        if mode not in LEASE_MODES:
            raise ValueError(f"GPU lease mode must be one of {sorted(LEASE_MODES)}")
        if not owner:
            raise ValueError("GPU lease owner is required")
        self.path = Path(path)
        self.mode = mode
        self.owner = owner
        self._held = False

    def acquire(self, *, timeout_s: float = 0.0, poll_s: float = 0.25) -> LeaseRecord:
        deadline = time.monotonic() + timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = LeaseRecord(
            mode=self.mode,
            owner=self.owner,
            pid=os.getpid(),
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = json.dumps(record.__dict__, sort_keys=True).encode("utf-8")
        while True:
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._held = True
                return record
            except FileExistsError:
                if time.monotonic() >= deadline:
                    current = self.inspect()
                    raise LeaseBusyError(f"GPU lease is held by {current}") from None
                time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    def inspect(self) -> Mapping[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def release(self) -> None:
        if not self._held:
            return
        current = self.inspect()
        if current.get("pid") != os.getpid() or current.get("owner") != self.owner:
            raise RuntimeError("refusing to release a GPU lease owned by another process")
        self.path.unlink()
        self._held = False

    def __enter__(self) -> "GPULease":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


class LocalJobRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT PRIMARY KEY,
                    parent_run_id TEXT,
                    mode TEXT NOT NULL CHECK(mode IN ('index','train','serve')),
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    yield_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    heartbeat_at TEXT,
                    checkpoint_path TEXT,
                    exit_code INTEGER,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_state_created
                ON jobs(state, created_at);
                """
            )

    def enqueue(
        self,
        *,
        run_id: str,
        mode: str,
        payload: Mapping[str, Any],
        parent_run_id: str | None = None,
    ) -> None:
        if mode not in LEASE_MODES:
            raise ValueError(f"mode must be one of {sorted(LEASE_MODES)}")
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    run_id,parent_run_id,mode,state,payload_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    parent_run_id,
                    mode,
                    "queued",
                    json.dumps(payload, sort_keys=True),
                    now,
                    now,
                ),
            )

    def claim_next(self, mode: str) -> Mapping[str, Any] | None:
        if mode not in LEASE_MODES:
            raise ValueError(f"mode must be one of {sorted(LEASE_MODES)}")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE state='queued' AND mode=? ORDER BY created_at LIMIT 1",
                (mode,),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE jobs SET state='running',updated_at=?,heartbeat_at=? WHERE run_id=?",
                (now, now, row["run_id"]),
            )
            result = dict(row)
            result["state"] = "running"
            return result

    def start(self, run_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET state='running',updated_at=?,heartbeat_at=?
                WHERE run_id=? AND state IN ('queued','running')
                """,
                (now, now, run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("job is absent or not resumable")

    def heartbeat(
        self,
        run_id: str,
        *,
        checkpoint_path: str | None = None,
        telemetry: Mapping[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload_json,state FROM jobs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["state"] in TERMINAL_JOB_STATES:
                raise RuntimeError("cannot heartbeat a terminal job")
            payload = json.loads(row["payload_json"])
            if telemetry is not None:
                payload["last_telemetry"] = dict(telemetry)
            connection.execute(
                """
                UPDATE jobs SET payload_json=?,heartbeat_at=?,updated_at=?,
                    checkpoint_path=COALESCE(?,checkpoint_path)
                WHERE run_id=?
                """,
                (json.dumps(payload, sort_keys=True), now, now, checkpoint_path, run_id),
            )

    def request_yield(self, run_id: str) -> None:
        self._update_flag(run_id, "yield_requested", 1)

    def yield_requested(self, run_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT yield_requested FROM jobs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return bool(row["yield_requested"])

    def mark_yielded(self, run_id: str, checkpoint_path: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE jobs SET state='queued',yield_requested=0,checkpoint_path=?,
                    updated_at=? WHERE run_id=?
                """,
                (checkpoint_path, now, run_id),
            )

    def complete(
        self, run_id: str, *, exit_code: int, error: str | None = None
    ) -> None:
        state = "succeeded" if exit_code == 0 else "failed"
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE jobs SET state=?,exit_code=?,error=?,updated_at=?
                WHERE run_id=?
                """,
                (state, exit_code, error, now, run_id),
            )

    def get(self, run_id: str) -> Mapping[str, Any]:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def _update_flag(self, run_id: str, name: str, value: int) -> None:
        if name != "yield_requested":
            raise ValueError("unsupported flag")
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {name}=?,updated_at=? WHERE run_id=?",
                (value, now, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)
