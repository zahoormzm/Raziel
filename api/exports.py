"""Local preview/evidence export service with canonical trace manifests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.pipeline import LocalRetrievalPipeline
from output.clips import ExtractionRequest, build_ffmpeg_command
from output.manifest import build_export_manifest, write_manifest


class LocalExportService:
    def __init__(
        self,
        *,
        pipeline: LocalRetrievalPipeline,
        output_root: str | Path,
        ffmpeg_binary: str | Path,
        ffprobe_binary: str | Path,
        operating_point_path: str | Path,
        pipeline_version: str = "working-tree-uncommitted",
    ) -> None:
        self.pipeline = pipeline
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_binary = str(Path(ffmpeg_binary).resolve())
        self.ffprobe_binary = str(Path(ffprobe_binary).resolve())
        self.operating_point_path = Path(operating_point_path).resolve()
        self.pipeline_version = pipeline_version
        self._records: dict[str, dict[str, Path]] = {}

    def export(self, body: dict[str, Any]) -> dict[str, Any]:
        search_id = str(body.get("search_id") or "")
        candidate_id = str(body.get("match_id") or "")
        mode = str(body.get("mode") or "")
        result = self.pipeline.result(search_id)
        if result is None:
            raise KeyError("unknown search_id")
        outcomes = (
            *result.verified_matches,
            *result.unresolved_visual,
            *result.unresolved_system,
            *result.rejected_near_misses,
        )
        outcome = next(
            (item for item in outcomes if item.candidate_id == candidate_id),
            None,
        )
        if outcome is None:
            raise KeyError("candidate does not belong to search")
        source = self.pipeline.connection.execute(
            """
            SELECT path,sha256,timebase,camera_id,duration_s
            FROM videos WHERE video_id=?
            """,
            (outcome.video_id,),
        ).fetchone()
        if source is None:
            raise KeyError("source video is not in the archive")
        source_path = Path(str(source["path"])).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        export_id = uuid4().hex
        directory = self.output_root / export_id
        directory.mkdir(parents=True, exist_ok=False)
        clip_path = directory / f"{mode}.mp4"
        padding_s = float(body.get("context_padding_s", 0.0))
        request = ExtractionRequest(
            source_path=source_path,
            output_path=clip_path,
            start_pts=outcome.t0,
            end_pts=outcome.t1,
            mode=mode,
            padding_s=padding_s,
        )
        command = build_ffmpeg_command(request, ffmpeg_binary=self.ffmpeg_binary)
        subprocess.run(command, check=True, capture_output=True, text=True)
        if not clip_path.is_file() or clip_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce a non-empty clip")

        actual_duration = self._duration(clip_path)
        actual_start = (
            self._preceding_keyframe(source_path, request.padded_start)
            if mode == "preview"
            else request.padded_start
        )
        source_duration = float(source["duration_s"] or outcome.t1)
        actual_end = min(source_duration, actual_start + actual_duration)
        manifest = build_export_manifest(
            export_id=export_id,
            search_id=search_id,
            source_file_id=outcome.video_id,
            camera_id=source["camera_id"],
            source_sha256=str(source["sha256"]),
            request=request,
            actual_start_pts=actual_start,
            actual_end_pts=actual_end,
            ffmpeg_version=self._ffmpeg_version(),
            source_timebase=str(source["timebase"] or "unknown"),
            operating_point_config_hash=_sha256(self.operating_point_path),
            pipeline_git_commit=self.pipeline_version,
            model_revisions={"frame_retrieval": self.pipeline.encoder.revision},
            output_clip_sha256=_sha256(clip_path),
            ffmpeg_binary=self.ffmpeg_binary,
        )
        manifest_path = directory / "manifest.json"
        write_manifest(manifest_path, manifest)
        self._records[export_id] = {
            "clip": clip_path,
            "manifest": manifest_path,
        }
        return {
            "export_id": export_id,
            "mode": mode,
            "clip_url": f"/exports/{export_id}/clip",
            "manifest_url": f"/exports/{export_id}/manifest",
            "manifest_sha256": manifest.manifest_sha256,
            "output_clip_sha256": manifest.output_clip_sha256,
        }

    def resolve(self, export_id: str, kind: str) -> Path | None:
        if kind not in {"clip", "manifest"}:
            return None
        path = self._records.get(export_id, {}).get(kind)
        if path is None:
            # Fall back to the on-disk layout. Tracking exports only in an
            # in-process dict meant a restart turned every previously issued
            # manifest_url into a 404 while the clip and manifest sat under
            # output_root/<export_id>/. The manifest is the traceability record
            # for an extraction; it has to outlive the process that made it.
            path = self._resolve_on_disk(export_id, kind)
        return path if path is not None and path.is_file() else None

    def _resolve_on_disk(self, export_id: str, kind: str) -> Path | None:
        # export_id is generated by uuid4().hex; refuse anything else so a
        # crafted id cannot walk out of output_root.
        if not re.fullmatch(r"[0-9a-f]{32}", export_id):
            return None
        directory = self.output_root / export_id
        try:
            if not directory.resolve().is_relative_to(self.output_root):
                return None
        except OSError:
            return None
        if not directory.is_dir():
            return None
        manifest_path = directory / "manifest.json"
        if kind == "manifest":
            return manifest_path if manifest_path.is_file() else None

        # Clips are written as `{mode}.mp4`. Globbing and taking the first match
        # alphabetically would return evidence.mp4 for a preview request in any
        # directory holding both. Each export_id currently gets a fresh directory
        # with exactly one clip, so that is unreachable today -- but this is the
        # restart-recovery path, and the manifest beside it records the mode
        # exactly, so there is no reason to guess.
        if manifest_path.is_file():
            try:
                mode = json.loads(manifest_path.read_text(encoding="utf-8")).get(
                    "extraction_mode"
                )
            except (OSError, ValueError):
                mode = None
            if isinstance(mode, str) and mode:
                candidate = directory / f"{mode}.mp4"
                if candidate.is_file():
                    return candidate
        for mode in ("evidence", "preview"):
            candidate = directory / f"{mode}.mp4"
            if candidate.is_file():
                return candidate
        return None

    def _duration(self, path: Path) -> float:
        completed = subprocess.run(
            [
                self.ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        value = float(completed.stdout.strip())
        if value <= 0:
            raise RuntimeError("export duration is not positive")
        return value

    def _preceding_keyframe(self, path: Path, target: float) -> float:
        completed = subprocess.run(
            [
                self.ffprobe_binary,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-skip_frame",
                "nokey",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        # ffprobe prints the literal "N/A" for frames with no usable timestamp
        # (common with MPEG-TS and remuxed sources). The old guard only rejected
        # blank lines, so "N/A" reached float() and raised ValueError after the
        # clip had already been written and the export directory created.
        values: list[float] = []
        for line in completed.stdout.splitlines():
            token = line.strip().rstrip(",")
            if not token or token.upper() == "N/A":
                continue
            try:
                values.append(float(token))
            except ValueError:
                continue
        prior = [value for value in values if value <= target + 1e-6]
        return max(prior) if prior else 0.0

    def _ffmpeg_version(self) -> str:
        completed = subprocess.run(
            [self.ffmpeg_binary, "-version"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.splitlines()[0].strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
