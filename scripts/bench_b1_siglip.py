"""Real, cache-bypassed B1 sampling + SigLIP2 benchmark adapter.

The source may be synthetic for throughput, but that never becomes a semantic
or held-out score. Model load is reported separately from the warm pipeline.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from index.frame_embed import SigLIP2FrameEncoder, index_frame_batch
from index.stores import ArtifactGeneration, VersionedEmbeddingStore
from ingest.sampler import _decode_pyav, ingest_video


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--db", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--camera-id", default="benchmark-synthetic")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--output")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size <= 0:
        raise ValueError("batch size must be positive")
    source = Path(args.source).resolve()
    database = Path(args.db).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    store_root = Path(args.store).resolve()
    store_root.mkdir(parents=True, exist_ok=True)

    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("B1 CUDA benchmark requires a visible CUDA device")
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    load_started = time.perf_counter()
    encoder = SigLIP2FrameEncoder(
        args.model,
        revision=args.revision,
        device="cuda",
        local_files_only=True,
    )
    model_load_s = time.perf_counter() - load_started

    pipeline_started = time.perf_counter()
    ingested = ingest_video(
        source,
        database,
        camera_id=args.camera_id,
        embedding_model_revision=args.revision,
        ffprobe_binary=args.ffprobe,
    )
    ingest_s = time.perf_counter() - pipeline_started

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """
            SELECT frame_id,target_pts
            FROM sample_ticks
            WHERE video_id=? AND sampling_lane='base' AND frame_id IS NOT NULL
            ORDER BY target_pts
            """,
            (ingested.manifest.video_id,),
        ).fetchall()
        frame_id_by_tick = {round(float(target), 6): int(frame_id) for frame_id, target in rows}
        selected_ids: list[int] = []
        selected_frames: list[object] = []
        for pts, frame in _decode_pyav(source):
            tick = round(pts, 6)
            frame_id = frame_id_by_tick.get(tick)
            if frame_id is not None:
                selected_ids.append(frame_id)
                selected_frames.append(frame)
        if len(selected_ids) != ingested.coverage.expected_ticks:
            raise RuntimeError(
                f"decoded {len(selected_ids)} base ticks; expected {ingested.coverage.expected_ticks}"
            )

        generation = ArtifactGeneration(
            schema_version="1.0.0",
            kind="frame_embeddings",
            inputs={
                "source_sha256": ingested.manifest.sha256,
                "sampling_policy_version": ingested.manifest.sampling_policy_version,
                "model_revision": args.revision,
                "preprocessing": "siglip2-rgb-v1",
            },
        )
        store = VersionedEmbeddingStore(
            generation.directory(store_root),
            dimension=encoder.dimension,
            generation=generation,
        )
        embed_started = time.perf_counter()
        for offset in range(0, len(selected_ids), args.batch_size):
            index_frame_batch(
                connection=connection,
                store=store,
                encoder=encoder,
                frame_ids=selected_ids[offset : offset + args.batch_size],
                frames=selected_frames[offset : offset + args.batch_size],
            )
        store.finalize()
        torch.cuda.synchronize()
        embed_s = time.perf_counter() - embed_started
        total_pipeline_s = time.perf_counter() - pipeline_started
        embedded = connection.execute(
            "SELECT count(*) FROM sample_ticks WHERE video_id=? AND status='embedded'",
            (ingested.manifest.video_id,),
        ).fetchone()[0]
    finally:
        connection.close()

    duration_s = float(ingested.manifest.duration_s)
    report = {
        "benchmark_id": "B1",
        "source_sha256": ingested.manifest.sha256,
        "synthetic_throughput_fixture": True,
        "semantic_measurement": False,
        "model_revision": args.revision,
        "model_load_cold_s": model_load_s,
        "ingest_s": ingest_s,
        "embed_s": embed_s,
        "warm_pipeline_s": total_pipeline_s,
        "footage_duration_s": duration_s,
        "embedded_ticks": int(embedded),
        "expected_ticks": int(ingested.coverage.expected_ticks),
        "embed_fps": embedded / embed_s if embed_s else None,
        "real_time_multiple": duration_s / total_pipeline_s if total_pipeline_s else None,
        "peak_vram_gb": torch.cuda.max_memory_allocated() / 1024**3,
        "result_cache_hits": 0,
        "failures": 0,
        "status": "measured",
        "passed_preferred_5x": duration_s / total_pipeline_s >= 5.0,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
