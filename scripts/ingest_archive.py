"""RAZIEL video-memory CLI.

Examples:
  python scripts/ingest_archive.py init --db artifacts/archive.sqlite
  python scripts/ingest_archive.py ingest --db artifacts/archive.sqlite video.mp4
  python scripts/ingest_archive.py coverage --db artifacts/archive.sqlite
  python scripts/ingest_archive.py graph-build --db artifacts/archive.sqlite --video-id ...
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evidence.graph_build import GraphBuilder
from evidence.graph_store import GraphStore
from index.exact_score import NumpyExactScorer
from index.stores import ArtifactGeneration, VersionedEmbeddingStore
from ingest.sampler import ExpectedTickLedger, ingest_video


def _write_json(value: Any, path: str | None = None) -> None:
    if hasattr(value, "to_wire"):
        value = value.to_wire()
    elif hasattr(value, "__dict__"):
        value = value.__dict__
    payload = json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    if path:
        Path(path).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


def _cmd_init(args: argparse.Namespace) -> int:
    with GraphStore(args.db):
        pass
    _write_json({"database": str(Path(args.db).resolve()), "initialized": True})
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    # Initialize the complete schema once so gated lanes can be enabled later.
    with GraphStore(args.db):
        pass
    results = []
    for source in args.sources:
        result = ingest_video(
            source,
            args.db,
            camera_id=args.camera_id,
            interval_s=args.interval,
            pipeline_version=args.pipeline_version,
            decoder_version=args.decoder_version,
            preprocessing_version=args.preprocessing_version,
            embedding_model_revision=args.embedding_revision,
            ffprobe_binary=args.ffprobe,
            decode=not args.metadata_only,
            max_tick_distance_s=args.max_tick_distance,
            motion_enabled=args.motion,
            motion_threshold=args.motion_threshold,
            motion_dense_fps=args.motion_dense_fps,
            motion_padding_s=args.motion_padding,
        )
        results.append(result.manifest.to_wire())
    _write_json(results, args.output)
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    with ExpectedTickLedger(args.db) as ledger:
        coverage = ledger.coverage(args.video_id, args.lane)
    _write_json(coverage)
    return 0


def _cmd_graph_build(args: argparse.Namespace) -> int:
    with GraphStore(args.db) as store:
        builder = GraphBuilder(
            store,
            producer_version=args.producer_version,
            sampling_policy_version=args.sampling_policy_version,
            detector_version=args.detector_version,
            tracker_version=args.tracker_version,
            thresholds=json.loads(args.thresholds),
        )
        stats = builder.build_video(args.video_id, enabled=True)
    _write_json(stats)
    return 0


def _cmd_exact_score(args: argparse.Namespace) -> int:
    directory = Path(args.store)
    metadata = json.loads(
        (directory / VersionedEmbeddingStore.METADATA_NAME).read_text(
            encoding="utf-8"
        )
    )
    generation = ArtifactGeneration(
        schema_version=metadata["schema_version"],
        kind=metadata["kind"],
        inputs=metadata["generation_inputs"],
    )
    store = VersionedEmbeddingStore(
        directory,
        dimension=int(metadata["dimension"]),
        generation=generation,
        create=False,
    )
    query = json.loads(args.query)
    result = NumpyExactScorer(store, block_rows=args.block_rows).score(query)
    _write_json(result, args.output)
    return 0


def _open_embedding_store(directory: Path) -> VersionedEmbeddingStore:
    metadata = json.loads(
        (directory / VersionedEmbeddingStore.METADATA_NAME).read_text(
            encoding="utf-8"
        )
    )
    generation = ArtifactGeneration(
        schema_version=metadata["schema_version"],
        kind=metadata["kind"],
        inputs=metadata["generation_inputs"],
    )
    return VersionedEmbeddingStore(
        directory,
        dimension=int(metadata["dimension"]),
        generation=generation,
        create=False,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _cmd_replica_manifest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.resolve() == Path(args.output).resolve():
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "root": str(root),
        "files": files,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    _write_json(manifest, args.output)
    return 0


def _cmd_snapshot_db(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    destination = Path(args.destination).resolve()
    if source == destination:
        raise ValueError("source and destination database paths must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not args.replace:
        raise FileExistsError(
            "destination exists; pass --replace to retain a backup and update it"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.snapshot-",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    with contextlib.closing(sqlite3.connect(str(source))) as source_connection:
        with contextlib.closing(
            sqlite3.connect(str(temporary))
        ) as destination_connection:
            source_connection.backup(destination_connection)
            check = destination_connection.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise RuntimeError(f"replica integrity_check failed: {check}")
    if destination.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination.replace(destination.with_name(f"{destination.name}.previous-{stamp}"))
    temporary.replace(destination)
    _write_json(
        {
            "schema_version": "1.0",
            "source": str(source),
            "destination": str(destination),
            "sha256": _sha256(destination),
            "sqlite_backup_api": True,
        }
    )
    return 0


def _cmd_finalize_store(args: argparse.Namespace) -> int:
    store = _open_embedding_store(Path(args.store))
    digest = store.finalize()
    _write_json(
        {
            "schema_version": store.generation.schema_version,
            "store": str(store.directory.resolve()),
            "row_count": store.row_count,
            "matrix_sha256": digest,
            "generation_key": store.generation.key,
        }
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subcommands = root.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="initialize canonical SQLite schema")
    init.add_argument("--db", required=True)
    init.set_defaults(handler=_cmd_init)

    ingest = subcommands.add_parser("ingest", help="probe, hash, and sample sources")
    ingest.add_argument("--db", required=True)
    ingest.add_argument("sources", nargs="+")
    ingest.add_argument("--camera-id")
    ingest.add_argument("--interval", type=float, default=1.0)
    ingest.add_argument("--pipeline-version", default="ingest-v1")
    ingest.add_argument(
        "--decoder-version",
        help="explicit decoder build identifier; defaults to detected PyAV/FFmpeg versions",
    )
    ingest.add_argument("--preprocessing-version", default="rgb24-quality-v1")
    ingest.add_argument("--embedding-revision", default="not-indexed")
    ingest.add_argument("--ffprobe", default="ffprobe")
    ingest.add_argument("--metadata-only", action="store_true")
    ingest.add_argument("--max-tick-distance", type=float)
    ingest.add_argument("--motion", action="store_true")
    ingest.add_argument("--motion-threshold", type=float, default=0.12)
    ingest.add_argument("--motion-dense-fps", type=float, default=4.0)
    ingest.add_argument("--motion-padding", type=float, default=1.0)
    ingest.add_argument("--output")
    ingest.set_defaults(handler=_cmd_ingest)

    coverage = subcommands.add_parser("coverage", help="report ledger coverage")
    coverage.add_argument("--db", required=True)
    coverage.add_argument("--video-id")
    coverage.add_argument("--lane", choices=["base", "motion", "refine"])
    coverage.set_defaults(handler=_cmd_coverage)

    graph = subcommands.add_parser("graph-build", help="materialize typed evidence graph")
    graph.add_argument("--db", required=True)
    graph.add_argument("--video-id", required=True)
    graph.add_argument("--producer-version", required=True)
    graph.add_argument("--sampling-policy-version", required=True)
    graph.add_argument("--detector-version")
    graph.add_argument("--tracker-version")
    graph.add_argument("--thresholds", default="{}")
    graph.set_defaults(handler=_cmd_graph_build)

    score = subcommands.add_parser("exact-score", help="score every embedding row")
    score.add_argument("--store", required=True)
    score.add_argument("--query", required=True, help="JSON float vector")
    score.add_argument("--block-rows", type=int, default=65536)
    score.add_argument("--output")
    score.set_defaults(handler=_cmd_exact_score)

    replica = subcommands.add_parser(
        "replica-manifest", help="write checksums for replica synchronization"
    )
    replica.add_argument("--root", required=True)
    replica.add_argument("--output", required=True)
    replica.set_defaults(handler=_cmd_replica_manifest)

    snapshot = subcommands.add_parser(
        "snapshot-db", help="create a transactionally consistent SQLite replica"
    )
    snapshot.add_argument("--source", required=True)
    snapshot.add_argument("--destination", required=True)
    snapshot.add_argument("--replace", action="store_true")
    snapshot.set_defaults(handler=_cmd_snapshot_db)

    finalize = subcommands.add_parser(
        "finalize-store", help="seal an embedding matrix with a full byte hash"
    )
    finalize.add_argument("--store", required=True)
    finalize.set_defaults(handler=_cmd_finalize_store)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
