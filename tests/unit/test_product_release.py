from __future__ import annotations

import unittest
from pathlib import Path

from api.brand import load_brand_config
from api.health import FallbackMode, WorkerState, select_fallback
from api.jobs import JobKind, JobRegistry, JobState
from output.clips import ExtractionRequest, build_ffmpeg_command
from output.manifest import build_export_manifest


class ProductReleaseTests(unittest.TestCase):
    def test_brand_has_single_frozen_identity(self) -> None:
        brand = load_brand_config()
        self.assertEqual(brand["product_name"], "RAZIEL")
        self.assertEqual(brand["product_subtitle"], "Temporal Evidence Intelligence")
        self.assertEqual(brand["retrieval_name"], "Eyes of God")

    def test_job_progress_is_monotonic(self) -> None:
        jobs = JobRegistry()
        job = jobs.create(JobKind.QUERY, {"text": "bag"})
        jobs.start(job.job_id)
        jobs.update(job.job_id, stage="retrieval", progress=0.5)
        with self.assertRaises(ValueError):
            jobs.update(job.job_id, stage="regression", progress=0.25)
        done = jobs.complete(job.job_id, {"status": "ok"})
        self.assertEqual(done.state, JobState.COMPLETE)

    def test_gpu_failure_never_implies_verified(self) -> None:
        self.assertEqual(
            select_fallback(WorkerState.UNAVAILABLE, mlx_gate_passed=False),
            FallbackMode.RETRIEVAL_ONLY,
        )
        self.assertEqual(
            select_fallback(WorkerState.UNAVAILABLE, mlx_gate_passed=True),
            FallbackMode.MLX_VERIFIER,
        )

    def test_preview_and_evidence_commands_are_distinct(self) -> None:
        preview = ExtractionRequest(Path("source.mp4"), Path("preview.mp4"), 4, 8, "preview")
        evidence = ExtractionRequest(Path("source.mp4"), Path("evidence.mp4"), 4, 8, "evidence")
        self.assertIn("copy", build_ffmpeg_command(preview))
        self.assertIn("libx264", build_ffmpeg_command(evidence))
        preview_command = build_ffmpeg_command(preview)
        evidence_command = build_ffmpeg_command(evidence)
        self.assertLess(preview_command.index("-ss"), preview_command.index("-i"))
        self.assertGreater(evidence_command.index("-ss"), evidence_command.index("-i"))

    def test_manifest_canonical_hash_verifies(self) -> None:
        request = ExtractionRequest(Path("source.mp4"), Path("evidence.mp4"), 4, 8, "evidence")
        manifest = build_export_manifest(
            export_id="e1",
            search_id="s1",
            source_file_id="video-1",
            camera_id="gate-01",
            source_sha256="a" * 64,
            request=request,
            actual_start_pts=4.0,
            actual_end_pts=8.0,
            ffmpeg_version="fixture",
            source_timebase="1/1000",
            operating_point_config_hash="op-fixture",
            pipeline_git_commit="commit-fixture",
            model_revisions={},
            output_clip_sha256="b" * 64,
        )
        self.assertTrue(manifest.verify_manifest_hash())


if __name__ == "__main__":
    unittest.main()
