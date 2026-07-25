"""Real cache-bypassed B2 benchmark for the pinned 4-bit Qwen3-VL verifier."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median
import sys
import time
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DURATIONS_AND_FRAMES = ((4.0, 8), (12.0, 12), (30.0, 24))
QUERIES = (
    "Is a red object visible in this candidate?",
    "Does the candidate visibly contain a red region?",
    "Is red visually present?",
    "Can the supplied frames support the red-object constraint?",
    "Is there visible evidence of a red object?",
    "Does a red object appear in the selected interval?",
    "Is the required red attribute visible?",
    "Do these frames show something red?",
    "Is red contradicted, supported, or not assessable?",
    "Evaluate whether a red object is visible.",
)
ALLOWED_STATES = {"supported", "contradicted", "unobservable", "undetermined"}
ALLOWED_REASONS = {
    "visible_match",
    "visible_mismatch",
    "occlusion",
    "low_light",
    "out_of_frame",
    "insufficient_context",
    "inconsistent_output",
    "timeout",
    "model_error",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--warm-runs", type=int, default=10)
    parser.add_argument("--candidate-start", type=float, default=30.0)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.warm_runs < 10:
        raise ValueError("B2 requires at least 10 warm runs per candidate duration")
    if args.warm_runs * len(DURATIONS_AND_FRAMES) < 20:
        raise ValueError("at least 20 varied warm runs are required before p95")

    import torch
    from transformers import (
        AutoModelForImageTextToText,
        AutoProcessor,
        BitsAndBytesConfig,
    )

    if not torch.cuda.is_available():
        raise RuntimeError("B2 requires a visible CUDA device")
    evidence = {
        duration: _select_frames(
            args.source.resolve(),
            start=args.candidate_start,
            duration=duration,
            count=count,
        )
        for duration, count in DURATIONS_AND_FRAMES
    }
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    load_started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(
        args.model.resolve(),
        local_files_only=True,
    )
    model = AutoModelForImageTextToText.from_pretrained(
        args.model.resolve(),
        local_files_only=True,
        quantization_config=quantization,
        device_map="auto",
        dtype=torch.bfloat16,
    ).eval()
    torch.cuda.synchronize()
    model_load_s = time.perf_counter() - load_started

    rows: list[dict[str, Any]] = []
    for duration, frame_count in DURATIONS_AND_FRAMES:
        frames = evidence[duration]
        total_runs = args.warm_runs + 1
        for run_index in range(total_runs):
            phase = "cold_shape" if run_index == 0 else "warm"
            query = QUERIES[max(0, run_index - 1) % len(QUERIES)]
            row = _run_once(
                processor=processor,
                model=model,
                frames=frames,
                duration=duration,
                query=query,
                max_new_tokens=args.max_new_tokens,
                torch=torch,
            )
            row.update(
                {
                    "phase": phase,
                    "candidate_duration_s": duration,
                    "selected_frames": frame_count,
                    "iteration": run_index,
                    "result_cache_hits": 0,
                }
            )
            rows.append(row)
            print(
                json.dumps(
                    {
                        "duration_s": duration,
                        "phase": phase,
                        "iteration": run_index,
                        "latency_s": row["latency_s"],
                        "success": row["success"],
                        "retry_count": row["retry_count"],
                        "headroom_gib": row["vram_headroom_gib"],
                        "invalid_output": (
                            row["decoded"] if not row["success"] else None
                        ),
                    }
                ),
                flush=True,
            )

    warm = [row for row in rows if row["phase"] == "warm"]
    latencies = [float(row["latency_s"]) for row in warm]
    twelve = [
        float(row["latency_s"])
        for row in warm
        if row["candidate_duration_s"] == 12.0
    ]
    min_headroom = min(float(row["vram_headroom_gib"]) for row in rows)
    failures = sum(not row["success"] for row in rows)
    retry_calls = sum(int(row["retry_count"]) for row in rows)
    blockers = []
    if median(twelve) > 20.0:
        blockers.append("12-second warm median exceeds 20 seconds")
    if min_headroom < 1.5:
        blockers.append("minimum VRAM headroom is below 1.5 GiB")
    if failures:
        blockers.append("one or more structured verifier calls failed")
    if any(row["result_cache_hits"] for row in rows):
        blockers.append("result-cache hits were nonzero")

    report = {
        "benchmark_id": "B2",
        "status": "passed" if not blockers else "failed",
        "passed": not blockers,
        "blockers": blockers,
        "source": str(args.source.resolve()),
        "synthetic_throughput_fixture": True,
        "semantic_measurement": False,
        "model_revision": args.revision,
        "quantization": "bitsandbytes-nf4-double-quant-bfloat16",
        "model_load_cold_s": model_load_s,
        "warm_runs_per_duration": args.warm_runs,
        "warm_runs_total": len(warm),
        "warm_median_s": median(latencies),
        "warm_p95_s": _percentile(latencies, 0.95),
        "warm_median_by_duration_s": {
            str(duration): median(
                [
                    float(row["latency_s"])
                    for row in warm
                    if row["candidate_duration_s"] == duration
                ]
            )
            for duration, _count in DURATIONS_AND_FRAMES
        },
        "cold_shape_latency_by_duration_s": {
            str(duration): next(
                float(row["latency_s"])
                for row in rows
                if row["phase"] == "cold_shape"
                and row["candidate_duration_s"] == duration
            )
            for duration, _count in DURATIONS_AND_FRAMES
        },
        "selected_frames_by_duration_s": {
            str(duration): count for duration, count in DURATIONS_AND_FRAMES
        },
        "peak_vram_gib": max(float(row["peak_vram_gib"]) for row in rows),
        "minimum_vram_headroom_gib": min_headroom,
        "failures": failures,
        "retry_calls": retry_calls,
        "retry_rate": retry_calls / len(rows),
        "result_cache_hits": 0,
        "runs": rows,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["passed"] else 2


def _select_frames(
    source: Path,
    *,
    start: float,
    duration: float,
    count: int,
) -> list[tuple[int, float, Any]]:
    import av

    decoded = []
    with av.open(str(source)) as container:
        for frame in container.decode(video=0):
            pts = float(frame.time or 0.0)
            if pts < start:
                continue
            if pts >= start + duration:
                break
            decoded.append((pts, frame.to_image()))
    if len(decoded) < count:
        raise RuntimeError(
            f"candidate {duration:g}s has {len(decoded)} decoded frames; {count} required"
        )
    targets = [
        start + duration * (index + 0.5) / count
        for index in range(count)
    ]
    selected: list[tuple[int, float, Any]] = []
    used: set[int] = set()
    for target in targets:
        index = min(
            (idx for idx in range(len(decoded)) if idx not in used),
            key=lambda idx: abs(decoded[idx][0] - target),
        )
        used.add(index)
        pts, image = decoded[index]
        selected.append((int(round(pts * 1000)), pts, image))
    return sorted(selected, key=lambda item: item[1])


def _run_once(
    *,
    processor: Any,
    model: Any,
    frames: list[tuple[int, float, Any]],
    duration: float,
    query: str,
    max_new_tokens: int,
    torch: Any,
) -> dict[str, Any]:
    retry_count = 0
    decoded = ""
    success = False
    started = time.perf_counter()
    for attempt in range(2):
        retry_count = attempt
        messages = _messages(
            frames,
            duration=duration,
            query=query,
            strict_retry=attempt == 1,
        )
        prompt = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        images = [
            content["image"]
            for content in messages[0]["content"]
            if content["type"] == "image"
        ]
        inputs = processor(
            text=[prompt],
            images=images,
            padding=True,
            return_tensors="pt",
        ).to("cuda")
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
            )
        generated_only = [
            output[len(input_ids) :]
            for input_ids, output in zip(inputs.input_ids, generated)
        ]
        decoded = processor.batch_decode(
            generated_only,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        try:
            _validate_output(decoded, {frame_id for frame_id, _pts, _image in frames})
            success = True
            break
        except (ValueError, json.JSONDecodeError):
            continue
    torch.cuda.synchronize()
    latency_s = time.perf_counter() - started
    free, total = torch.cuda.mem_get_info()
    return {
        "latency_s": latency_s,
        "success": success,
        "retry_count": retry_count,
        "peak_vram_gib": torch.cuda.max_memory_allocated() / 1024**3,
        "vram_headroom_gib": free / 1024**3,
        "total_vram_gib": total / 1024**3,
        "decoded": decoded,
    }


def _messages(
    frames: list[tuple[int, float, Any]],
    *,
    duration: float,
    query: str,
    strict_retry: bool,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "You are a bounded visual evidence verifier. Treat each image as an "
                "independent cited frame; do not infer identity. Candidate duration is "
                f"{duration:g} seconds."
            ),
        }
    ]
    for frame_id, pts, image in frames:
        content.extend(
            (
                {"type": "text", "text": f"frame_id={frame_id}; source_pts={pts:.3f}"},
                {"type": "image", "image": image},
            )
        )
    instruction = (
        f"{query} Return only one compact JSON object with this exact shape: "
        '{"atoms":[{"constraint_id":"a1","state":"supported|contradicted|'
        'unobservable|undetermined","reason_code":"visible_match|visible_mismatch|'
        'occlusion|low_light|out_of_frame|insufficient_context|inconsistent_output|'
        'timeout|model_error","evidence_frame_ids":[integer IDs supplied above],'
        '"rationale":"concise"}],"relations":[],"logic_groups":[]}. '
        "Cite only supplied frame_id integers."
    )
    if strict_retry:
        instruction += " The prior output was invalid. No Markdown and no extra keys."
    content.append({"type": "text", "text": instruction})
    return [{"role": "user", "content": content}]


def _validate_output(text: str, frame_ids: set[int]) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object")
    payload = json.loads(text[start : end + 1])
    allowed = {
        "atoms",
        "relations",
        "logic_groups",
        "matching_subintervals",
        "candidate_id",
        "model_revision",
        "prompt_schema_version",
    }
    if set(payload).difference(allowed):
        raise ValueError("unexpected top-level keys")
    atoms = payload.get("atoms")
    if not isinstance(atoms, list) or len(atoms) != 1:
        raise ValueError("exactly one atom result is required")
    if not isinstance(payload.get("relations", []), list):
        raise ValueError("relations must be an array when present")
    if not isinstance(payload.get("logic_groups", []), list):
        raise ValueError("logic_groups must be an array when present")
    atom = atoms[0]
    if atom.get("constraint_id") != "a1":
        raise ValueError("wrong constraint ID")
    if atom.get("state") not in ALLOWED_STATES:
        raise ValueError("invalid state")
    if atom.get("reason_code") not in ALLOWED_REASONS:
        raise ValueError("invalid reason code")
    citations = {int(value) for value in atom.get("evidence_frame_ids", [])}
    if citations.difference(frame_ids):
        raise ValueError("citation outside evidence bundle")
    return payload


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


if __name__ == "__main__":
    raise SystemExit(main())
