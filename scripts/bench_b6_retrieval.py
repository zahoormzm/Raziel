"""B6 latency adapter for the currently staged retrieval-only tier.

This measures real exact retrieval against the 10-minute B1 archive. It never
reports a verified-result latency because the Qwen verifier gate has not run.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from api.pipeline import LocalRetrievalPipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
FRAME_STORE_KEY = "eefad77e98144f7e025edc87bafede19ec3fb02b40a423cb37fabbc99f6e810b"
QUERIES = (
    "a black backpack near the gate",
    "a person in a red jacket",
    "a person places a bag on the ground",
    "a person in red places a black bag near the gate and walks away",
    "a person enters near the gate, then later leaves carrying a bag",
    "a person carrying a yellow umbrella",
    "the colour of the bag in the dark corner",
    "a person wearing a red or blue hat",
    "no bag left in the corridor",
    "how many people are waiting by the bench",
    "a dark backpack by the gate",
    "someone wearing a red coat",
    "someone sets a bag on the floor",
    "a red-clothed person sets a black bag by the gate then leaves",
    "someone comes in by the gate and afterwards exits with a bag",
    "someone with a yellow umbrella",
    "what colour is the bag in the dark corner",
    "someone in a red or blue cap",
    "the corridor has no bag left in it",
    "count the people waiting at the bench",
)
_PIPELINE: LocalRetrievalPipeline | None = None


def _pipeline() -> LocalRetrievalPipeline:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = LocalRetrievalPipeline(
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
            model_revision=MODEL_REVISION,
            thresholds={
                "whole_query_frame": 0.10,
                "candidate_anchor_frame": 0.10,
                "rare_attribute_frame": 0.10,
            },
            operating_point_label="synthetic-latency-smoke-explicit-0.10",
        )
    return _PIPELINE


def benchmark(
    *,
    benchmark_id: str,
    candidate_duration_s: float | None,
    cold: bool,
    iteration: int,
    bypass_result_cache: bool,
) -> dict[str, Any]:
    del candidate_duration_s, cold
    if benchmark_id != "B6":
        raise ValueError("this adapter implements only B6")
    if not bypass_result_cache:
        raise ValueError("B6 must bypass result caches")
    query = QUERIES[iteration % len(QUERIES)]
    started = time.perf_counter()
    result = _pipeline().query(
        {
            "text": query,
            "start_time": 0,
            "end_time": 600,
            "max_episode_count": 256,
        }
    )
    retrieval_s = time.perf_counter() - started
    return {
        "success": True,
        "latency_s": retrieval_s,
        "retrieval_feedback_s": retrieval_s,
        "verified_result_s": None,
        "result_cache_hits": 0,
        "query_family_index": iteration % 10,
        "semantic_measurement": False,
        "synthetic_throughput_fixture": True,
        "verifier_configured": False,
        "archive_conclusion": result.archive_conclusion,
        "exact_scoring_completed": result.candidate_generation.exact_scoring_completed,
    }
