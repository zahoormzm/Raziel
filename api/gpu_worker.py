"""Bounded client for the RTX 5070 verification worker."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from packages.contracts.verification import VerificationResult

from .health import HealthSnapshot, WorkerState, snapshot


@dataclass(frozen=True, slots=True)
class GPUWorkerConfig:
    base_url: str
    health_timeout_s: float = 1.5
    verify_timeout_s: float = 60.0
    slow_health_ms: float = 750.0
    expected_model_revision: str | None = None
    expected_operating_point_hash: str | None = None


class GPUWorkerClient:
    def __init__(self, config: GPUWorkerConfig) -> None:
        self.config = config

    def health(self, *, mlx_gate_passed: bool = False) -> HealthSnapshot:
        started = time.perf_counter()
        try:
            payload = self._json_request("GET", "/health", None, self.config.health_timeout_s)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return snapshot(
                WorkerState.UNAVAILABLE,
                mlx_gate_passed=mlx_gate_passed,
                detail=type(exc).__name__,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        mismatch = self._revision_mismatch(payload)
        if mismatch:
            return snapshot(
                WorkerState.UNAVAILABLE,
                mlx_gate_passed=mlx_gate_passed,
                latency_ms=latency_ms,
                detail=mismatch,
            )
        state = (
            WorkerState.SLOW
            if latency_ms > self.config.slow_health_ms
            else WorkerState.HEALTHY
        )
        return snapshot(
            state,
            mlx_gate_passed=mlx_gate_passed,
            latency_ms=latency_ms,
            model_revision=payload.get("model_revision"),
            operating_point_hash=payload.get("operating_point_hash"),
        )

    def verify(self, evidence_bundle: dict[str, Any]) -> VerificationResult:
        if "query_plan" not in evidence_bundle or "frames" not in evidence_bundle:
            raise ValueError("worker bundle requires query_plan and bounded frames")
        response = self._json_request(
            "POST",
            "/verify",
            evidence_bundle,
            self.config.verify_timeout_s,
        )
        result = VerificationResult.model_validate(response)
        supplied_ids = {
            int(frame["frame_id"])
            for frame in evidence_bundle["frames"]
            if "frame_id" in frame
        }
        result.validate_citations(supplied_ids)
        return result

    def _revision_mismatch(self, payload: dict[str, Any]) -> str | None:
        expected_model = self.config.expected_model_revision
        if expected_model and payload.get("model_revision") != expected_model:
            return "model_revision_mismatch"
        expected_op = self.config.expected_operating_point_hash
        if expected_op and payload.get("operating_point_hash") != expected_op:
            return "operating_point_hash_mismatch"
        return None

    def _json_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        encoded = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=encoded,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            raise ValueError("worker response must be a JSON object")
        return payload
