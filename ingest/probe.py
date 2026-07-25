"""FFprobe integration that preserves the complete raw response."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping


class FFProbeError(RuntimeError):
    """Raised when a source cannot be probed."""


@dataclass(frozen=True)
class VideoProbe:
    raw: Mapping[str, Any]
    duration_s: float | None
    time_base: str | None
    recording_start: str | None
    video_stream_index: int | None

    def raw_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True, separators=(",", ":"))


def _first_video_stream(raw: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for stream in raw.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def parse_probe(raw: Mapping[str, Any]) -> VideoProbe:
    stream = _first_video_stream(raw)
    fmt = raw.get("format") or {}
    duration = _finite_float((stream or {}).get("duration"))
    if duration is None:
        duration = _finite_float(fmt.get("duration"))
    tags = {}
    tags.update(fmt.get("tags") or {})
    tags.update((stream or {}).get("tags") or {})
    recording_start = (
        tags.get("creation_time")
        or tags.get("com.apple.quicktime.creationdate")
        or tags.get("date")
    )
    time_base = (stream or {}).get("time_base")
    if time_base:
        try:
            Fraction(time_base)
        except (ValueError, ZeroDivisionError) as exc:
            raise FFProbeError(f"invalid video time_base: {time_base!r}") from exc
    return VideoProbe(
        raw=dict(raw),
        duration_s=duration,
        time_base=time_base,
        recording_start=recording_start,
        video_stream_index=(stream or {}).get("index"),
    )


def probe_video(
    path: str | Path,
    *,
    ffprobe_binary: str = "ffprobe",
    timeout_s: float = 60.0,
) -> VideoProbe:
    source = Path(path)
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-show_chapters",
        "-show_programs",
        "-print_format",
        "json",
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFProbeError(f"ffprobe failed for {source}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or f"exit code {result.returncode}"
        raise FFProbeError(f"ffprobe failed for {source}: {message}")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFProbeError(f"ffprobe returned invalid JSON for {source}") from exc
    return parse_probe(raw)
