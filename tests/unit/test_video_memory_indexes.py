from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from evidence.graph_store import GraphStore
from index.exact_score import NumpyExactScorer
from index.clip_embed import (
    ClipEmbeddingIndex,
    ClipSpan,
    index_clip_batch,
    overlapping_clips,
)
from index.frame_embed import FrameEmbeddingIndex, index_frame_batch
from index.stores import ArtifactGeneration, ResumableBlockState, VersionedEmbeddingStore
from ingest.probe import parse_probe
from ingest.sampler import ExpectedTickLedger


class TinyEncoder:
    dimension = 2
    revision = "tiny-test-only"

    def encode(self, frames):
        return frames

    def encode_text(self, texts):
        vocabulary = {"right": [1.0, 0.0], "up": [0.0, 1.0]}
        return [vocabulary[text] for text in texts]


class TinyClipEncoder:
    dimension = 2
    revision = "tiny-clip-test-only"

    def encode(self, clips):
        return clips


def _store(tmp_path: Path) -> VersionedEmbeddingStore:
    generation = ArtifactGeneration(
        schema_version="1.0",
        kind="frame_embeddings",
        inputs={"source_hash": "a" * 64, "model_revision": "test"},
    )
    return VersionedEmbeddingStore(
        generation.directory(tmp_path), dimension=2, generation=generation
    )


def test_versioned_store_and_exact_score_vector_length(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert list(store.append([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])) == [0, 1, 2]
    reopened = VersionedEmbeddingStore(
        store.directory,
        dimension=2,
        generation=store.generation,
        create=False,
    )
    result = NumpyExactScorer(reopened, block_rows=2).score([1.0, 0.0])
    assert result.row_ids == (0, 1, 2)
    assert len(result.scores) == reopened.row_count == 3
    assert result.scores[0] == pytest.approx(1.0)
    assert result.scores[1] == pytest.approx(0.0)
    assert result.exact_scoring_completed is True


def test_uncommitted_matrix_tail_is_repaired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append([[1.0, 0.0]])
    with store.matrix_path.open("ab") as handle:
        handle.write(b"\x00" * store.row_size_bytes)
    reopened = VersionedEmbeddingStore(
        store.directory,
        dimension=2,
        generation=store.generation,
        create=False,
    )
    assert reopened.row_count == 1
    assert reopened.matrix_path.stat().st_size == reopened.row_size_bytes


def test_rebuilt_embedding_artifact_hashes_are_reproducible(tmp_path: Path) -> None:
    generation = ArtifactGeneration(
        schema_version="1.0",
        kind="frame_embeddings",
        inputs={"source_hash": "d" * 64, "model_revision": "test"},
    )
    stores = [
        VersionedEmbeddingStore(
            generation.directory(tmp_path / name),
            dimension=2,
            generation=generation,
        )
        for name in ("first", "second")
    ]
    for store in stores:
        store.append([[1.0, 0.0], [0.0, 1.0]])
        store.finalize()
    assert stores[0].matrix_path.read_bytes() == stores[1].matrix_path.read_bytes()
    assert stores[0].metadata_path.read_bytes() == stores[1].metadata_path.read_bytes()


def test_frame_metadata_commits_after_durable_embedding(tmp_path: Path) -> None:
    database = tmp_path / "archive.sqlite"
    with GraphStore(database):
        pass
    with ExpectedTickLedger(database) as ledger:
        ledger.register_video(
            video_id="v",
            path="v.mp4",
            sha256="a" * 64,
            probe=parse_probe(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "index": 0,
                            "time_base": "1/1000",
                            "duration": "1",
                        }
                    ]
                }
            ),
            camera_id="cam",
            pipeline_version="v1",
            cache_key="cache",
        )
        ledger.plan("v", [0.0])
        frame_id = ledger.record_decoded_frame(
            video_id="v", target_pts=0.0, actual_pts=0.01, lane="base"
        )
        store = _store(tmp_path / "store")
        rows = index_frame_batch(
            connection=ledger.connection,
            store=store,
            encoder=TinyEncoder(),
            frame_ids=[frame_id],
            frames=[[3.0, 4.0]],
        )
        assert list(rows) == [0]
        assert ledger.coverage("v").embedded_ticks == 1
        assert store.read_rows()[0] == pytest.approx([0.6, 0.8])
        ticks, score = FrameEmbeddingIndex(
            ledger.connection, store, block_rows=1
        ).exact_score_text("right", TinyEncoder(), video_ids=["v"])
        assert len(ticks) == len(score.scores) == 1
        assert score.scores[0] == pytest.approx(0.6)


def test_resumable_block_state_generation_and_atomic_progress(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    state = ResumableBlockState(path, generation_key="generation-a")
    state.complete("block-0001")
    assert ResumableBlockState(path, generation_key="generation-a").is_complete(
        "block-0001"
    )
    with pytest.raises(ValueError, match="generation"):
        ResumableBlockState(path, generation_key="generation-b")


def test_clip_index_is_gated_versioned_and_exact(tmp_path: Path) -> None:
    database = tmp_path / "clips.sqlite"
    with GraphStore(database) as graph:
        generation = ArtifactGeneration(
            schema_version="1.0",
            kind="clip_embeddings",
            inputs={"source_hash": "c" * 64, "model_revision": "clip-test"},
        )
        store = VersionedEmbeddingStore(
            generation.directory(tmp_path), dimension=2, generation=generation
        )
        spans = overlapping_clips("v", 18.0, clip_s=12.0, stride_s=6.0)
        assert spans == [
            ClipSpan("v", 0.0, 12.0),
            ClipSpan("v", 6.0, 18.0),
        ]
        with pytest.raises(RuntimeError, match="disabled"):
            index_clip_batch(
                connection=graph.connection,
                store=store,
                encoder=TinyClipEncoder(),
                spans=spans,
                clips=[[1.0, 0.0], [0.0, 1.0]],
                enabled=False,
            )
        index_clip_batch(
            connection=graph.connection,
            store=store,
            encoder=TinyClipEncoder(),
            spans=spans,
            clips=[[1.0, 0.0], [0.0, 1.0]],
            enabled=True,
        )
        scoped, result = ClipEmbeddingIndex(
            graph.connection, store, block_rows=1
        ).exact_score([1.0, 0.0], video_ids=["v"])
        assert len(scoped) == len(result.scores) == 2
        assert result.scores == pytest.approx((1.0, 0.0))
        assert len(store.finalize()) == 64
