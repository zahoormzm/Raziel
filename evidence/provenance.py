"""Canonical provenance records and artifact hashes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceProvenance:
    video_id: str
    source_sha256: str
    t0: float
    t1: float
    producer_version: str
    frame_ids: tuple[int, ...] = ()
    artifact_generation: str | None = None

    def __post_init__(self) -> None:
        if len(self.source_sha256) != 64:
            raise ValueError("source_sha256 must be a complete SHA-256 hex digest")
        int(self.source_sha256, 16)
        if self.t0 < 0 or self.t1 < self.t0:
            raise ValueError("invalid provenance interval")
        if not self.producer_version:
            raise ValueError("producer_version is required")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["frame_ids"] = list(self.frame_ids)
        payload["provenance_hash"] = canonical_hash(payload)
        return payload


def edge_evidence(
    *,
    video_id: str,
    source_sha256: str,
    t0: float,
    t1: float,
    producer_version: str,
    frame_ids: Iterable[int],
    relationship_basis: str,
    artifact_generation: str | None = None,
) -> dict[str, Any]:
    provenance = EvidenceProvenance(
        video_id=video_id,
        source_sha256=source_sha256,
        t0=t0,
        t1=t1,
        producer_version=producer_version,
        frame_ids=tuple(sorted(set(frame_ids))),
        artifact_generation=artifact_generation,
    ).as_dict()
    provenance["relationship_basis"] = relationship_basis
    return provenance


def validate_inspectable_evidence(payload: Mapping[str, Any]) -> None:
    required = {"video_id", "source_sha256", "t0", "t1", "producer_version"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"evidence provenance is missing: {sorted(missing)}")
    EvidenceProvenance(
        video_id=str(payload["video_id"]),
        source_sha256=str(payload["source_sha256"]),
        t0=float(payload["t0"]),
        t1=float(payload["t1"]),
        producer_version=str(payload["producer_version"]),
        frame_ids=tuple(int(value) for value in payload.get("frame_ids", [])),
        artifact_generation=payload.get("artifact_generation"),
    )
