"""Day-one hardware benchmark harness with cache-bypass assertions.

The harness accepts either real measurement JSONL or a user-supplied adapter
(``module:callable``).  It does not import or download any model by itself.
Unmeasured gates remain ``not_measured``.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib
import json
import math
import os
import platform
from pathlib import Path
from statistics import median
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


BENCHMARK_IDS = ("B1", "B2", "B3", "B4", "B5", "B6", "B7")


@dataclass(frozen=True)
class BenchmarkSummary:
    benchmark_id: str
    status: str
    passed: bool
    blockers: tuple[str, ...]
    cold_latency_s: float | None
    warm_runs: int
    warm_median_s: float | None
    warm_p95_s: float | None
    peak_vram_gb: float | None
    minimum_vram_headroom_gb: float | None
    failures: int
    result_cache_hits: int
    measurements: Mapping[str, Any]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--measurements-jsonl")
    source.add_argument("--adapter", help="real benchmark callable as module:callable")
    parser.add_argument("--benchmarks", nargs="+", choices=BENCHMARK_IDS, default=list(BENCHMARK_IDS))
    parser.add_argument("--warm-runs", type=int, default=20)
    parser.add_argument("--output")
    parser.add_argument("--semantic-gain-b3", action="store_true")
    parser.add_argument("--declared-b4-min-clips-per-second", type=float)
    parser.add_argument("--declared-b7-min-processed-fps", type=float)
    parser.add_argument("--environment-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    environment = environment_record()
    rows: list[Mapping[str, Any]] = []
    if args.measurements_jsonl:
        rows = load_measurements(args.measurements_jsonl)
    elif args.adapter and not args.environment_only:
        adapter = load_adapter(args.adapter)
        rows = run_adapter(adapter, args.benchmarks, args.warm_runs)
    summaries = [
        summarize(
            benchmark_id,
            [row for row in rows if row.get("benchmark_id") == benchmark_id],
            semantic_gain_b3=args.semantic_gain_b3,
            declared_b4_min_rate=args.declared_b4_min_clips_per_second,
            declared_b7_min_fps=args.declared_b7_min_processed_fps,
        )
        for benchmark_id in args.benchmarks
    ]
    report = {
        "environment": environment,
        "discipline": {
            "cold_reported_separately": True,
            "minimum_warm_runs_for_median": 10,
            "minimum_varied_warm_runs_for_p95": 20,
            "result_cache_bypass_required": True,
        },
        "summaries": [asdict(summary) for summary in summaries],
    }
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(encoded + "\n", encoding="utf-8")
    return 0


def load_measurements(path: str | Path) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("benchmark_id") not in BENCHMARK_IDS:
                raise ValueError(f"{path}:{line_number}: unknown benchmark_id")
            rows.append(row)
    return rows


def load_adapter(specification: str) -> Callable[..., Mapping[str, Any]]:
    if ":" not in specification:
        raise ValueError("adapter must use module:callable syntax")
    module_name, attribute = specification.split(":", 1)
    function = getattr(importlib.import_module(module_name), attribute)
    if not callable(function):
        raise TypeError("benchmark adapter is not callable")
    return function


def run_adapter(
    adapter: Callable[..., Mapping[str, Any]],
    benchmark_ids: Sequence[str],
    warm_runs: int,
) -> list[Mapping[str, Any]]:
    if warm_runs < 20:
        raise ValueError("at least 20 varied warm runs are required before reporting p95")
    rows: list[Mapping[str, Any]] = []
    for benchmark_id in benchmark_ids:
        durations = (4.0, 12.0, 30.0) if benchmark_id in {"B2", "B3"} else (None,)
        for duration in durations:
            cold = dict(
                adapter(
                    benchmark_id=benchmark_id,
                    candidate_duration_s=duration,
                    cold=True,
                    iteration=0,
                    bypass_result_cache=True,
                )
            )
            cold.update(
                {
                    "benchmark_id": benchmark_id,
                    "candidate_duration_s": duration,
                    "phase": "cold",
                }
            )
            rows.append(cold)
            for iteration in range(warm_runs):
                warm = dict(
                    adapter(
                        benchmark_id=benchmark_id,
                        candidate_duration_s=duration,
                        cold=False,
                        iteration=iteration,
                        bypass_result_cache=True,
                    )
                )
                warm.update(
                    {
                        "benchmark_id": benchmark_id,
                        "candidate_duration_s": duration,
                        "phase": "warm",
                    }
                )
                rows.append(warm)
    return rows


def summarize(
    benchmark_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    semantic_gain_b3: bool,
    declared_b4_min_rate: float | None,
    declared_b7_min_fps: float | None,
) -> BenchmarkSummary:
    if not rows:
        return BenchmarkSummary(
            benchmark_id=benchmark_id,
            status="not_measured",
            passed=False,
            blockers=("no cache-bypassed measurements supplied",),
            cold_latency_s=None,
            warm_runs=0,
            warm_median_s=None,
            warm_p95_s=None,
            peak_vram_gb=None,
            minimum_vram_headroom_gb=None,
            failures=0,
            result_cache_hits=0,
            measurements={},
        )
    cache_hits = sum(int(row.get("result_cache_hits", 0)) for row in rows)
    failures = sum(not bool(row.get("success", True)) for row in rows)
    warm = [row for row in rows if row.get("phase") == "warm" and row.get("success", True)]
    cold = [row for row in rows if row.get("phase") == "cold" and row.get("success", True)]
    warm_latencies = [float(row["latency_s"]) for row in warm if row.get("latency_s") is not None]
    cold_latencies = [float(row["latency_s"]) for row in cold if row.get("latency_s") is not None]
    blockers: list[str] = []
    if cache_hits:
        blockers.append("result cache hits were nonzero")
    if len(warm_latencies) < 10:
        blockers.append("fewer than 10 warm runs; warm median is not reportable")
    warm_median = median(warm_latencies) if len(warm_latencies) >= 10 else None
    warm_p95 = _percentile(warm_latencies, 0.95) if len(warm_latencies) >= 20 else None
    if len(warm_latencies) < 20:
        blockers.append("fewer than 20 varied warm runs; p95 is not reportable")
    peak_vram_values = [
        float(row["peak_vram_gb"]) for row in rows if row.get("peak_vram_gb") is not None
    ]
    headroom_values = [
        float(row["total_vram_gb"]) - float(row["peak_vram_gb"])
        for row in rows
        if row.get("total_vram_gb") is not None and row.get("peak_vram_gb") is not None
    ]
    measurements: dict[str, Any] = {}

    if benchmark_id in {"B2", "B3"}:
        twelve = [
            float(row["latency_s"])
            for row in warm
            if float(row.get("candidate_duration_s", -1)) == 12.0
            and row.get("latency_s") is not None
        ]
        twelve_median = median(twelve) if len(twelve) >= 10 else None
        measurements["12s_warm_median_s"] = twelve_median
        if twelve_median is None or twelve_median > 20:
            blockers.append("12-second warm median exceeds 20 seconds or is unmeasured")
        if not headroom_values or min(headroom_values) < 1.5:
            blockers.append("minimum measured VRAM headroom is below 1.5 GB or unmeasured")
        if benchmark_id == "B3" and not semantic_gain_b3:
            blockers.append("held-out semantic gain for the 8B verifier is not established")
    elif benchmark_id == "B1":
        rates = [float(row["realtime_factor"]) for row in warm if row.get("realtime_factor")]
        measurements["median_realtime_factor"] = median(rates) if rates else None
        if not rates or median(rates) < 5:
            blockers.append("preferred 5x real-time ingest/embed throughput is not met or unmeasured")
    elif benchmark_id == "B4":
        rates = [float(row["clips_per_second"]) for row in warm if row.get("clips_per_second")]
        measurements["median_clips_per_second"] = median(rates) if rates else None
        if declared_b4_min_rate is None:
            blockers.append("practical pre-event clip-indexing rate is not declared")
        elif not rates or median(rates) < declared_b4_min_rate:
            blockers.append("native clip indexing is below the declared practical rate")
    elif benchmark_id == "B5":
        if warm_median is None or warm_median > 5:
            blockers.append("parser warm median exceeds 5 seconds or is unmeasured")
    elif benchmark_id == "B6":
        retrieval = [
            float(row["retrieval_feedback_s"])
            for row in warm
            if row.get("retrieval_feedback_s") is not None
        ]
        verified = [
            float(row["verified_result_s"])
            for row in warm
            if row.get("verified_result_s") is not None
        ]
        measurements.update(
            {
                "retrieval_feedback_median_s": median(retrieval) if retrieval else None,
                "verified_result_median_s": median(verified) if verified else None,
                "verified_result_max_s": max(verified) if verified else None,
            }
        )
        if not retrieval or median(retrieval) > 3:
            blockers.append("retrieval feedback exceeds 3 seconds or is unmeasured")
        if not verified or median(verified) > 30:
            blockers.append("verified-result median exceeds the 30-second target or is unmeasured")
        if not verified or max(verified) > 60:
            blockers.append("verified-result latency exceeds the 60-second hard demo ceiling")
    elif benchmark_id == "B7":
        rates = [float(row["processed_fps"]) for row in warm if row.get("processed_fps")]
        resume_hash_match = all(bool(row.get("resume_hash_match", False)) for row in rows)
        identity_claims = any(bool(row.get("identity_claim_emitted", False)) for row in rows)
        measurements.update(
            {
                "median_processed_fps": median(rates) if rates else None,
                "resume_hash_match": resume_hash_match,
                "identity_claims": identity_claims,
            }
        )
        if declared_b7_min_fps is None:
            blockers.append("practical overnight archive rate is not declared")
        elif not rates or median(rates) < declared_b7_min_fps:
            blockers.append("detection/tracklet rate is below the declared overnight rate")
        if not resume_hash_match:
            blockers.append("checkpoint/resume artifact hashes do not reproduce")
        if identity_claims:
            blockers.append("tracklet benchmark emitted an identity claim")

    return BenchmarkSummary(
        benchmark_id=benchmark_id,
        status="passed" if not blockers else "failed",
        passed=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        cold_latency_s=min(cold_latencies) if cold_latencies else None,
        warm_runs=len(warm_latencies),
        warm_median_s=warm_median,
        warm_p95_s=warm_p95,
        peak_vram_gb=max(peak_vram_values) if peak_vram_values else None,
        minimum_vram_headroom_gb=min(headroom_values) if headroom_values else None,
        failures=failures,
        result_cache_hits=cache_hits,
        measurements=measurements,
    )


def environment_record() -> Mapping[str, Any]:
    return {
        "os": platform.platform(),
        "python": sys.version,
        "machine": platform.machine(),
        "processor": platform.processor(),
        "pid": os.getpid(),
        "gpu": _nvidia_smi(),
        "status": "hardware_identity_only; performance not inferred",
    }


def _nvidia_smi() -> Mapping[str, Any] | None:
    import subprocess

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    rows = []
    for line in result.stdout.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) == 4:
            rows.append(
                {
                    "name": values[0],
                    "memory_total_mib": values[1],
                    "driver_version": values[2],
                    "temperature_c": values[3],
                }
            )
    return {"devices": rows}


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main())
