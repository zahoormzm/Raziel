"""Health and failover policy for the two-node demo runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class WorkerState(StrEnum):
    HEALTHY = "healthy"
    SLOW = "slow"
    UNAVAILABLE = "unavailable"
    UNCONFIGURED = "unconfigured"


class FallbackMode(StrEnum):
    FULL_GPU = "full_gpu"
    RESTRICTED_GPU = "restricted_gpu"
    MLX_VERIFIER = "mlx_verifier"
    RETRIEVAL_ONLY = "retrieval_only"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    worker_state: WorkerState
    fallback_mode: FallbackMode
    checked_at: str
    latency_ms: float | None = None
    model_revision: str | None = None
    operating_point_hash: str | None = None
    detail: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


def select_fallback(
    worker_state: WorkerState,
    *,
    mlx_gate_passed: bool,
) -> FallbackMode:
    if worker_state == WorkerState.HEALTHY:
        return FallbackMode.FULL_GPU
    if worker_state == WorkerState.SLOW:
        return FallbackMode.RESTRICTED_GPU
    if mlx_gate_passed:
        return FallbackMode.MLX_VERIFIER
    return FallbackMode.RETRIEVAL_ONLY


def snapshot(
    worker_state: WorkerState,
    *,
    mlx_gate_passed: bool = False,
    **details: Any,
) -> HealthSnapshot:
    return HealthSnapshot(
        worker_state=worker_state,
        fallback_mode=select_fallback(worker_state, mlx_gate_passed=mlx_gate_passed),
        checked_at=datetime.now(timezone.utc).isoformat(),
        **details,
    )
