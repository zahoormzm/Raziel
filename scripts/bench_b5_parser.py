"""Cache-bypassed B5 adapter for the deterministic bounded parser."""

from __future__ import annotations

import time
from typing import Any

from query.parser import deterministic_parse


QUERIES = (
    "a person with a black bag",
    "a red vehicle near the gate",
    "someone picks up a box",
    "a person places a bag near the entrance",
    "a person walks away after placing a bag",
    "a blue object before a red object",
    "no visible person in the declared interval",
    "two people near a vehicle",
    "a person wearing red",
    "a bag by the doorway",
    "someone carries a package",
    "a person follows another person",
    "a vehicle enters the scene",
    "a person exits after picking up a box",
    "a black bag and a red shirt",
    "a person at the gate",
    "a package near a vehicle",
    "someone walks away",
    "a person picks up and carries a bag",
    "a person near the entrance with a box",
)


def benchmark(
    *,
    benchmark_id: str,
    candidate_duration_s: float | None,
    cold: bool,
    iteration: int,
    bypass_result_cache: bool,
) -> dict[str, Any]:
    del candidate_duration_s, cold
    if benchmark_id != "B5":
        raise ValueError("this adapter implements only B5")
    if not bypass_result_cache:
        raise ValueError("B5 must bypass result caches")
    query = QUERIES[iteration % len(QUERIES)]
    started = time.perf_counter()
    plan = deterministic_parse(
        query,
        {
            "camera_ids": (f"camera-{iteration % 3}",),
            "start_time": float(iteration),
            "end_time": float(iteration + 60),
        },
    )
    latency_s = time.perf_counter() - started
    return {
        "success": bool(plan.query_text and plan.parser_version),
        "latency_s": latency_s,
        "result_cache_hits": 0,
        "query_family_index": iteration % len(QUERIES),
        "parser_version": plan.parser_version,
        "fallback": "deterministic",
    }
