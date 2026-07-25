"""Ingestion boundary contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from .base import ContractModel, utc_now


class SamplingLane(StrEnum):
    BASE = "base"
    MOTION = "motion"
    REFINE = "refine"


class TickStatus(StrEnum):
    EMBEDDED = "embedded"
    DECODE_FAILED = "decode_failed"
    SKIPPED = "skipped"


class FrameTick(ContractModel):
    video_id: str = Field(min_length=1)
    target_pts: float = Field(ge=0)
    sampling_lane: SamplingLane
    status: TickStatus
    frame_id: int | None = Field(default=None, ge=0)
    actual_pts: float | None = Field(default=None, ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "FrameTick":
        if self.status == TickStatus.EMBEDDED and self.frame_id is None:
            raise ValueError("embedded ticks require frame_id")
        if self.status == TickStatus.DECODE_FAILED and not self.error_code:
            raise ValueError("decode_failed ticks require error_code")
        return self


class VideoManifest(ContractModel):
    video_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    camera_id: str | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ffprobe_json: dict[str, Any]
    recording_start: datetime | None = None
    timebase: str | None = None
    duration_s: float = Field(ge=0)
    ingested_at: datetime = Field(default_factory=utc_now)
    pipeline_version: str = Field(min_length=1)
    cache_key: str = Field(min_length=1)
    sampling_policy_version: str = Field(min_length=1)
    expected_ticks: int = Field(ge=0)
    embedded_ticks: int = Field(ge=0)
    decode_failed_ticks: int = Field(ge=0)
    skipped_ticks: int = Field(ge=0)
    ticks: list[FrameTick] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tick_ledger(self) -> "VideoManifest":
        accounted = self.embedded_ticks + self.decode_failed_ticks + self.skipped_ticks
        if accounted != self.expected_ticks:
            raise ValueError("expected_ticks must equal embedded + decode_failed + skipped")
        if self.ticks:
            if len(self.ticks) != self.expected_ticks:
                raise ValueError("tick ledger length must equal expected_ticks")
            if any(tick.video_id != self.video_id for tick in self.ticks):
                raise ValueError("all ticks must reference the manifest video_id")
        return self

    @property
    def scored_coverage(self) -> float:
        return self.embedded_ticks / self.expected_ticks if self.expected_ticks else 0.0
