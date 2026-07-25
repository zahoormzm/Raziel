"""Serve the staged RTX 5070 retrieval-only tier with an explicit threshold."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.health import WorkerState, snapshot
from api.cluster_verifier import HTTPClusterVerifier
from api.exports import LocalExportService
from api.gpu_worker import GPUWorkerClient, GPUWorkerConfig
from api.main import RazielService, create_app
from api.pipeline import LocalRetrievalPipeline


PINNED_SIGLIP_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
PINNED_STORE_KEY = "eefad77e98144f7e025edc87bafede19ec3fb02b40a423cb37fabbc99f6e810b"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold",
        type=float,
        required=True,
        help="explicit development cosine threshold; no production default exists",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "b1" / "archive.sqlite",
    )
    parser.add_argument(
        "--frame-store",
        type=Path,
        default=(
            PROJECT_ROOT
            / "artifacts"
            / "b1"
            / "stores"
            / "frame_embeddings"
            / PINNED_STORE_KEY
        ),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT / "models" / "siglip2-base-patch16-224",
    )
    parser.add_argument("--model-revision", default=PINNED_SIGLIP_REVISION)
    parser.add_argument("--verifier-url")
    parser.add_argument("--verifier-revision")
    parser.add_argument("--operating-point-hash")
    parser.add_argument(
        "--ffmpeg",
        type=Path,
        default=(
            PROJECT_ROOT
            / "tools"
            / "ffmpeg"
            / "ffmpeg-master-latest-win64-gpl"
            / "bin"
            / "ffmpeg.exe"
        ),
    )
    parser.add_argument(
        "--ffprobe",
        type=Path,
        default=(
            PROJECT_ROOT
            / "tools"
            / "ffmpeg"
            / "ffmpeg-master-latest-win64-gpl"
            / "bin"
            / "ffprobe.exe"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.verifier_url and (
        not args.verifier_revision or not args.operating_point_hash
    ):
        parser.error(
            "--verifier-url requires --verifier-revision and --operating-point-hash"
        )

    pipeline = LocalRetrievalPipeline(
        database=args.database,
        frame_store=args.frame_store,
        model_path=args.model,
        model_revision=args.model_revision,
        thresholds={
            "whole_query_frame": args.threshold,
            "candidate_anchor_frame": args.threshold,
            "rare_attribute_frame": args.threshold,
        },
        operating_point_label=f"development-explicit-{args.threshold:g}",
    )
    worker = None
    if args.verifier_url:
        pipeline.candidate_verifier = HTTPClusterVerifier(
            connection=pipeline.connection,
            endpoint=args.verifier_url.rstrip("/") + "/verify",
            model_revision=args.verifier_revision,
            operating_point_hash=args.operating_point_hash,
            asset_root=PROJECT_ROOT / "artifacts" / "evidence_frames",
            cache_path=PROJECT_ROOT / "artifacts" / "verification_cache.sqlite3",
        )
        worker = GPUWorkerClient(
            GPUWorkerConfig(
                base_url=args.verifier_url,
                expected_model_revision=args.verifier_revision,
                expected_operating_point_hash=args.operating_point_hash,
            )
        )
    exports = LocalExportService(
        pipeline=pipeline,
        output_root=PROJECT_ROOT / "artifacts" / "exports",
        ffmpeg_binary=args.ffmpeg,
        ffprobe_binary=args.ffprobe,
        operating_point_path=PROJECT_ROOT / "config" / "operating_point.yaml",
    )
    service = RazielService(
        query_handler=pipeline.query,
        export_handler=exports.export,
        export_resolver=exports.resolve,
        media_resolver=pipeline.media_path,
        coverage_handler=pipeline.coverage_report,
        query_interpreter=pipeline.parse,
        async_queries=True,
        health_handler=(
            (lambda: worker.health().to_wire())
            if worker is not None
            else lambda: snapshot(
                WorkerState.UNAVAILABLE,
                model_revision=args.model_revision,
                detail="Verifier not configured; exact retrieval-only tier is active.",
            ).to_wire()
        ),
    )
    app = create_app(service)
    try:
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        pipeline.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
