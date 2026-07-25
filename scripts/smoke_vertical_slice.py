"""Run one real retrieval -> Qwen verification -> export vertical slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.cluster_verifier import HTTPClusterVerifier
from api.exports import LocalExportService
from api.gpu_worker import GPUWorkerClient, GPUWorkerConfig
from api.pipeline import LocalRetrievalPipeline


SIGLIP_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
QWEN_REVISION = "ebb281ec70b05090aa6165b016eac8ec08e71b17"
FRAME_STORE_KEY = "eefad77e98144f7e025edc87bafede19ec3fb02b40a423cb37fabbc99f6e810b"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verifier-url", default="http://127.0.0.1:8010")
    parser.add_argument("--operating-point-hash", required=True)
    parser.add_argument("--threshold", required=True, type=float)
    parser.add_argument("--query", default="a red object")
    parser.add_argument("--start", type=float, default=30.0)
    parser.add_argument("--end", type=float, default=34.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    worker = GPUWorkerClient(
        GPUWorkerConfig(
            base_url=args.verifier_url,
            expected_model_revision=QWEN_REVISION,
            expected_operating_point_hash=args.operating_point_hash,
        )
    )
    health = worker.health()
    if health.worker_state != "healthy":
        raise RuntimeError(f"verifier worker is not healthy: {health.to_wire()}")

    pipeline = LocalRetrievalPipeline(
        database=PROJECT_ROOT / "artifacts" / "b1" / "archive.sqlite",
        frame_store=(
            PROJECT_ROOT
            / "artifacts"
            / "b1"
            / "stores"
            / "frame_embeddings"
            / FRAME_STORE_KEY
        ),
        model_path=PROJECT_ROOT / "models" / "siglip2-base-patch16-224",
        model_revision=SIGLIP_REVISION,
        thresholds={
            "whole_query_frame": args.threshold,
            "candidate_anchor_frame": args.threshold,
            "rare_attribute_frame": args.threshold,
        },
        operating_point_label=f"synthetic-vertical-smoke-explicit-{args.threshold:g}",
    )
    try:
        pipeline.candidate_verifier = HTTPClusterVerifier(
            connection=pipeline.connection,
            endpoint=args.verifier_url.rstrip("/") + "/verify",
            model_revision=QWEN_REVISION,
            operating_point_hash=args.operating_point_hash,
            asset_root=PROJECT_ROOT / "artifacts" / "evidence_frames",
            cache_path=PROJECT_ROOT / "artifacts" / "vertical_verification_cache.sqlite3",
        )
        result = pipeline.query(
            {
                "text": args.query,
                "start_time": args.start,
                "end_time": args.end,
            }
        )
        outcome = next(
            iter(
                [
                    *result.verified_matches,
                    *result.unresolved_visual,
                    *result.unresolved_system,
                    *result.rejected_near_misses,
                ]
            ),
            None,
        )
        export_result = None
        if outcome is not None:
            exporter = LocalExportService(
                pipeline=pipeline,
                output_root=PROJECT_ROOT / "artifacts" / "vertical_exports",
                ffmpeg_binary=(
                    PROJECT_ROOT
                    / "tools"
                    / "ffmpeg"
                    / "ffmpeg-master-latest-win64-gpl"
                    / "bin"
                    / "ffmpeg.exe"
                ),
                ffprobe_binary=(
                    PROJECT_ROOT
                    / "tools"
                    / "ffmpeg"
                    / "ffmpeg-master-latest-win64-gpl"
                    / "bin"
                    / "ffprobe.exe"
                ),
                operating_point_path=PROJECT_ROOT / "config" / "operating_point.yaml",
            )
            export_result = exporter.export(
                {
                    "search_id": result.search_id,
                    "match_id": outcome.candidate_id,
                    "mode": "evidence",
                }
            )
        report = {
            "status": "passed" if result.verified_matches and export_result else "failed",
            "synthetic_functional_smoke": True,
            "semantic_measurement": False,
            "worker_health": health.to_wire(),
            "headline": result.headline(),
            "scope": result.scope,
            "indexing": result.indexing.to_wire(),
            "candidate_generation": result.candidate_generation.to_wire(),
            "verification": result.verification.to_wire(),
            "verified_matches": len(result.verified_matches),
            "unresolved_visual": len(result.unresolved_visual),
            "unresolved_system": len(result.unresolved_system),
            "rejected_near_misses": len(result.rejected_near_misses),
            "archive_conclusion": result.archive_conclusion,
            "export": export_result,
        }
    finally:
        pipeline.close()
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
