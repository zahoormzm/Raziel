"""FastAPI adapter for the local orchestrator.

The orchestration core remains importable without FastAPI. `create_app` performs
the optional import so contract and pipeline tests remain runnable offline.
"""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Callable

from packages.contracts.search_result import SearchResult

from .brand import load_brand_config
from .health import WorkerState, snapshot
from .jobs import JobKind, JobRegistry


def _default_benchmark_panel() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "eval" / "reports" / "benchmark_panel.json"
    if path.is_file():
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        # The panel carries no top-level `status` key at all -- the measured
        # signal lives per configuration under configurations[*].status -- so
        # both the old unconditional assignment and a setdefault would report
        # "not_yet_measured" forever, even once real results exist. Derive it the
        # same way eval/panel.py does.
        configurations = payload.get("configurations", {}) or {}
        measured = any(
            isinstance(section, dict) and section.get("status") == "measured"
            for section in configurations.values()
        )
        payload["status"] = "measured" if measured else "not_yet_measured"
        return payload
    return {
        "status": "not_yet_measured",
        "config_hash": None,
        "evaluated_at": None,
    }


class RazielService:
    def __init__(
        self,
        *,
        registry: JobRegistry | None = None,
        ingest_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        query_handler: Callable[[dict[str, Any]], SearchResult | dict[str, Any]] | None = None,
        export_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        media_resolver: Callable[[str], Path | None] | None = None,
        coverage_handler: Callable[[], dict[str, Any]] | None = None,
        current_benchmark: dict[str, Any] | None = None,
        health_handler: Callable[[], dict[str, Any]] | None = None,
        export_resolver: Callable[[str, str], Path | None] | None = None,
        query_interpreter: Callable[[dict[str, Any]], Any] | None = None,
        async_queries: bool = False,
    ) -> None:
        self.registry = registry or JobRegistry()
        self.ingest_handler = ingest_handler
        self.query_handler = query_handler
        self.export_handler = export_handler
        self.media_resolver = media_resolver
        self.coverage_handler = coverage_handler
        self.current_benchmark = current_benchmark or _default_benchmark_panel()
        self.health_handler = health_handler
        self.export_resolver = export_resolver
        self.query_interpreter = query_interpreter
        self.async_queries = async_queries

    def submit(self, kind: JobKind, request: dict[str, Any]) -> dict[str, Any]:
        record = self.registry.create(kind, request)
        return record.to_wire()

    def run_sync(self, kind: JobKind, request: dict[str, Any]) -> dict[str, Any]:
        record = self.registry.create(kind, request)
        return self._execute(record.job_id, kind, request)

    def run_async(self, kind: JobKind, request: dict[str, Any]) -> dict[str, Any]:
        record = self.registry.create(kind, request)
        thread = threading.Thread(
            target=self._execute,
            args=(record.job_id, kind, dict(request)),
            daemon=True,
            name=f"raziel-{kind.value}-{record.job_id[:8]}",
        )
        thread.start()
        return self.registry.get(record.job_id).to_wire()

    def _execute(
        self,
        job_id: str,
        kind: JobKind,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        self.registry.start(
            job_id,
            stage="retrieval" if kind == JobKind.QUERY else "started",
        )
        handlers = {
            JobKind.INGEST: self.ingest_handler,
            JobKind.QUERY: self.query_handler,
            JobKind.EXPORT: self.export_handler,
        }
        handler = handlers[kind]
        if handler is None:
            return self.registry.fail(
                job_id,
                f"{kind.value} handler is not configured",
            ).to_wire()
        try:
            raw = handler(request)
            if isinstance(raw, SearchResult):
                result = raw.to_wire()
                result["headline"] = raw.headline()
            else:
                result = dict(raw)
            return self.registry.complete(job_id, result).to_wire()
        except Exception as exc:
            return self.registry.fail(job_id, f"{type(exc).__name__}: {exc}").to_wire()

    def coverage(self) -> dict[str, Any]:
        if self.coverage_handler is not None:
            return self.coverage_handler()
        completed = [
            job
            for job in self.registry.list()
            if job.kind == JobKind.INGEST and job.result is not None
        ]
        return {"sources": [job.result for job in completed], "source_count": len(completed)}


def create_app(service: RazielService | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - environment gate
        raise RuntimeError(
            "FastAPI runtime is not installed; install requirements/core.lock"
        ) from exc

    app = FastAPI(
        title="RAZIEL",
        description="Local-first Temporal Evidence Intelligence",
        version="0.1.0",
    )
    state = service or RazielService()
    ui_root = Path(__file__).resolve().parents[1] / "ui" / "web"

    @app.get("/health")
    def health() -> dict[str, Any]:
        if state.health_handler is not None:
            return state.health_handler()
        return snapshot(
            WorkerState.UNCONFIGURED,
            detail="GPU worker health must be wired by the event profile",
        ).to_wire()

    @app.post("/ingest")
    def ingest(body: dict[str, Any]) -> dict[str, Any]:
        return state.run_sync(JobKind.INGEST, body)

    @app.get("/ingest/{job_id}")
    def ingest_status(job_id: str) -> dict[str, Any]:
        try:
            return state.registry.get(job_id).to_wire()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.post("/query")
    def query(body: dict[str, Any]) -> dict[str, Any]:
        if state.async_queries:
            return state.run_async(JobKind.QUERY, body)
        return state.run_sync(JobKind.QUERY, body)

    @app.post("/query/interpret")
    def interpret_query(body: dict[str, Any]) -> dict[str, Any]:
        if state.query_interpreter is None:
            raise HTTPException(status_code=503, detail="query interpreter is not configured")
        interpretation = state.query_interpreter(body)
        if hasattr(interpretation, "to_wire"):
            return interpretation.to_wire()
        if hasattr(interpretation, "model_dump"):
            return interpretation.model_dump(mode="json")
        return dict(interpretation)

    @app.get("/query/{job_id}")
    def query_status(job_id: str) -> dict[str, Any]:
        try:
            return state.registry.get(job_id).to_wire()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="search not found") from exc

    @app.post("/export")
    def export(body: dict[str, Any]) -> dict[str, Any]:
        return state.run_sync(JobKind.EXPORT, body)

    @app.get("/exports/{export_id}/{kind}")
    def exported_artifact(export_id: str, kind: str) -> Any:
        if state.export_resolver is None:
            raise HTTPException(status_code=404, detail="export resolver is not configured")
        path = state.export_resolver(export_id, kind)
        if path is None:
            raise HTTPException(status_code=404, detail="export artifact not found")
        media_type = "application/json" if kind == "manifest" else None
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/coverage")
    def coverage() -> dict[str, Any]:
        return state.coverage()

    @app.get("/benchmark/current")
    def benchmark() -> dict[str, Any]:
        return state.current_benchmark

    @app.get("/config/brand")
    def brand() -> dict[str, str]:
        return load_brand_config()

    @app.get("/media/{video_id}")
    def media(video_id: str) -> Any:
        if state.media_resolver is None:
            raise HTTPException(status_code=404, detail="media resolver is not configured")
        path = state.media_resolver(video_id)
        if path is None:
            raise HTTPException(status_code=404, detail="video not found")
        return FileResponse(path)

    if ui_root.exists():
        app.mount("/assets", StaticFiles(directory=ui_root), name="assets")

        @app.get("/")
        def index() -> Any:
            return FileResponse(ui_root / "index.html")

    return app
