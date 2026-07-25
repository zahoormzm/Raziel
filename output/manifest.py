"""Traceable extraction manifest creation."""

from __future__ import annotations

from pathlib import Path

from packages.contracts.search_result import ExportManifest

from .clips import ExtractionRequest, build_ffmpeg_command


def build_export_manifest(
    *,
    export_id: str,
    search_id: str | None,
    source_file_id: str,
    camera_id: str | None,
    source_sha256: str,
    request: ExtractionRequest,
    actual_start_pts: float,
    actual_end_pts: float,
    ffmpeg_version: str,
    source_timebase: str,
    operating_point_config_hash: str,
    pipeline_git_commit: str,
    model_revisions: dict[str, str],
    output_clip_sha256: str,
    ffmpeg_binary: str = "ffmpeg",
) -> ExportManifest:
    if actual_end_pts <= actual_start_pts:
        raise ValueError("actual output interval is invalid")
    manifest = ExportManifest(
        export_id=export_id,
        search_id=search_id,
        source_file_id=source_file_id,
        camera_id=camera_id,
        source_sha256=source_sha256,
        requested_start_pts=request.start_pts,
        requested_end_pts=request.end_pts,
        actual_start_pts=actual_start_pts,
        actual_end_pts=actual_end_pts,
        context_padding_s=request.padding_s,
        extraction_mode=request.mode,
        ffmpeg_command=build_ffmpeg_command(request, ffmpeg_binary=ffmpeg_binary),
        ffmpeg_version=ffmpeg_version,
        source_timebase=source_timebase,
        operating_point_config_hash=operating_point_config_hash,
        pipeline_git_commit=pipeline_git_commit,
        model_revisions=model_revisions,
        output_clip_sha256=output_clip_sha256,
    )
    return manifest.with_computed_hash()


def write_manifest(path: Path, manifest: ExportManifest) -> None:
    """Persist a manifest only after its canonical hash is valid."""
    if not manifest.verify_manifest_hash():
        raise ValueError("manifest hash is missing or invalid")
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
