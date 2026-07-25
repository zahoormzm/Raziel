"""Deterministic FFmpeg command construction.

Commands are returned as argument lists and never shell-interpolated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    source_path: Path
    output_path: Path
    start_pts: float
    end_pts: float
    mode: str
    padding_s: float = 0.0

    def __post_init__(self) -> None:
        if self.start_pts < 0 or self.end_pts <= self.start_pts:
            raise ValueError("invalid extraction interval")
        if self.padding_s < 0:
            raise ValueError("padding_s cannot be negative")
        if self.mode not in {"preview", "evidence"}:
            raise ValueError("mode must be preview or evidence")

    @property
    def padded_start(self) -> float:
        return max(0.0, self.start_pts - self.padding_s)

    @property
    def padded_duration(self) -> float:
        return self.end_pts + self.padding_s - self.padded_start


def build_ffmpeg_command(
    request: ExtractionRequest,
    *,
    ffmpeg_binary: str = "ffmpeg",
) -> list[str]:
    common = [
        ffmpeg_binary,
        "-hide_banner",
        "-nostdin",
        "-y",
    ]
    if request.mode == "preview":
        # Input-side seek is fast and may align to the preceding keyframe.
        command = [
            *common,
            "-ss",
            f"{request.padded_start:.6f}",
            "-i",
            str(request.source_path),
            "-t",
            f"{request.padded_duration:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
        ]
    else:
        # Output-side seek decodes to the requested boundary before re-encoding.
        command = [
            *common,
            "-i",
            str(request.source_path),
            "-ss",
            f"{request.padded_start:.6f}",
            "-t",
            f"{request.padded_duration:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
        ]
    return [*command, str(request.output_path)]
