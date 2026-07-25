"""Exact-score aggregation for recall-first semantic retrieval channels."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import dataclass
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from query.schema import (
    CandidateWindowData,
    EvidenceRefData,
    QueryFilters,
    candidate_window_payload,
    coerce_query_filters,
    stable_identifier,
)


AGGREGATIONS = frozenset({"max", "mean", "top20_mean", "persistence"})


@dataclass(frozen=True)
class ScoreTick:
    tick_id: str
    frame_id: int
    video_id: str
    camera_id: str | None
    pts: float
    score: float
    producer_version: str = "exact-score-v1"


@dataclass(frozen=True)
class WindowSpan:
    window_id: str
    video_id: str
    camera_id: str | None
    t0: float
    t1: float
    zone_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelScoreInput:
    channel_id: str
    ticks: tuple[ScoreTick, ...]
    threshold: float
    aggregation: str = "top20_mean"
    persistence_frame_threshold: float = 0.0
    atom_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelRetrievalTrace:
    channel_id: str
    aggregation: str
    threshold: float
    expected_ticks: int
    scored_ticks: int
    windows_aggregated: int
    qualifying_windows: int
    exact_scoring_completed: bool


@dataclass(frozen=True)
class RetrievalOutput:
    candidates: tuple[CandidateWindowData, ...]
    traces: tuple[ChannelRetrievalTrace, ...]
    channels_run: tuple[str, ...]
    exact_scoring_completed: bool


def aggregate_values(
    values: Sequence[float],
    *,
    method: str = "top20_mean",
    persistence_frame_threshold: float = 0.0,
) -> float:
    if method not in AGGREGATIONS:
        raise ValueError(f"Unknown aggregation method: {method!r}")
    if not values:
        raise ValueError("cannot aggregate an empty score sequence")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("scores must be finite")
    if method == "max":
        return max(values)
    if method == "mean":
        return sum(values) / len(values)
    if method == "persistence":
        return float(sum(value >= persistence_frame_threshold for value in values))
    count = max(1, math.ceil(len(values) * 0.20))
    return sum(sorted(values, reverse=True)[:count]) / count


def apply_scope_filters(
    windows: Sequence[WindowSpan],
    filters: QueryFilters | Mapping[str, object] | None,
) -> tuple[WindowSpan, ...]:
    if filters is None:
        return tuple(windows)
    scope = coerce_query_filters(filters)
    return tuple(
        window
        for window in windows
        if (not scope.video_ids or window.video_id in scope.video_ids)
        and (not scope.camera_ids or window.camera_id in scope.camera_ids)
        and (scope.start_time is None or window.t1 >= scope.start_time)
        and (scope.end_time is None or window.t0 <= scope.end_time)
        and (
            not scope.zone_ids
            or bool(set(scope.zone_ids).intersection(window.zone_ids))
        )
    )


def _validate_exact_ticks(
    ticks: Sequence[ScoreTick], expected_tick_ids: Iterable[str] | None
) -> bool:
    tick_ids = [tick.tick_id for tick in ticks]
    if len(tick_ids) != len(set(tick_ids)):
        raise ValueError("score vector contains duplicate tick IDs")
    if expected_tick_ids is None:
        return True
    expected = tuple(expected_tick_ids)
    if len(expected) != len(set(expected)):
        raise ValueError("expected tick IDs contain duplicates")
    if set(tick_ids) != set(expected):
        missing = sorted(set(expected).difference(tick_ids))
        extra = sorted(set(tick_ids).difference(expected))
        raise ValueError(
            f"exact score vector mismatch; missing={missing[:5]}, extra={extra[:5]}"
        )
    return True


def aggregate_channel(
    channel: ChannelScoreInput,
    windows: Sequence[WindowSpan],
    *,
    expected_tick_ids: Iterable[str] | None = None,
) -> tuple[tuple[CandidateWindowData, ...], ChannelRetrievalTrace]:
    expected_ids = (
        None if expected_tick_ids is None else tuple(expected_tick_ids)
    )
    exact = _validate_exact_ticks(channel.ticks, expected_ids)

    # Bucket the ticks once per (video, camera) and bisect per window, instead of
    # rescanning every tick for every window. The old scan was O(windows x ticks):
    # a one-hour source at 1 fps produces ~3,600 ticks against ~1,800 windows
    # across the three scales, i.e. ~6.5M comparisons per channel, repeated for
    # the whole-query channel and every per-atom channel. This is O(T log T +
    # W log T) and returns the identical set.
    buckets: dict[tuple[str, str | None], list[Any]] = {}
    for tick in channel.ticks:
        buckets.setdefault((tick.video_id, tick.camera_id), []).append(tick)
    for bucket in buckets.values():
        bucket.sort(key=lambda tick: tick.pts)
    bucket_pts = {key: [tick.pts for tick in value] for key, value in buckets.items()}

    candidates: list[CandidateWindowData] = []
    considered = 0
    for window in windows:
        if window.t1 <= window.t0:
            raise ValueError(f"window {window.window_id} has an invalid interval")
        key = (window.video_id, window.camera_id)
        bucket = buckets.get(key, ())
        if bucket:
            pts_list = bucket_pts[key]
            lo = bisect_left(pts_list, window.t0)
            hi = bisect_left(pts_list, window.t1)
            relevant = bucket[lo:hi]
        else:
            relevant = []
        if not relevant:
            continue
        considered += 1
        score = aggregate_values(
            [tick.score for tick in relevant],
            method=channel.aggregation,
            persistence_frame_threshold=channel.persistence_frame_threshold,
        )
        if score < channel.threshold:
            continue
        evidence = tuple(
            EvidenceRefData(
                node_id=f"frame:{tick.frame_id}",
                node_type="frame",
                video_id=tick.video_id,
                camera_id=tick.camera_id,
                t0=tick.pts,
                t1=tick.pts,
                frame_ids=(tick.frame_id,),
                producer_version=tick.producer_version,
            )
            for tick in sorted(relevant, key=lambda item: item.score, reverse=True)[:3]
        )
        candidates.append(
            CandidateWindowData(
                candidate_id=stable_identifier(
                    "cw",
                    (
                        window.video_id,
                        window.camera_id,
                        window.t0,
                        window.t1,
                    ),
                ),
                video_id=window.video_id,
                camera_id=window.camera_id,
                t0=window.t0,
                t1=window.t1,
                channel_scores={channel.channel_id: score},
                qualifying_channels=(channel.channel_id,),
                evidence=evidence,
                atom_ids=channel.atom_ids,
                trace_ids=(window.window_id,),
            )
        )
    return (
        tuple(candidates),
        ChannelRetrievalTrace(
            channel_id=channel.channel_id,
            aggregation=channel.aggregation,
            threshold=channel.threshold,
            expected_ticks=(
                len(expected_ids)
                if expected_ids is not None
                else len(channel.ticks)
            ),
            scored_ticks=len(channel.ticks),
            windows_aggregated=considered,
            qualifying_windows=len(candidates),
            exact_scoring_completed=exact,
        ),
    )


def retrieval_evaluation_payload(output: RetrievalOutput) -> dict[str, object]:
    """Stable hooks for candidate-recall, volume, latency-side evaluation."""

    return {
        "channels_run": list(output.channels_run),
        "exact_scoring_completed": output.exact_scoring_completed,
        "qualifying_windows": len(
            {candidate.candidate_id for candidate in output.candidates}
        ),
        "channel_traces": [trace.__dict__.copy() for trace in output.traces],
        "candidate_intervals": [
            {
                "candidate_id": candidate.candidate_id,
                "video_id": candidate.video_id,
                "camera_id": candidate.camera_id,
                "t0": candidate.t0,
                "t1": candidate.t1,
                "qualifying_channels": list(candidate.qualifying_channels),
                "atom_ids": list(candidate.atom_ids),
            }
            for candidate in output.candidates
        ],
    }


def retrieve_channels(
    channels: Sequence[ChannelScoreInput],
    windows: Sequence[WindowSpan],
    *,
    expected_tick_ids_by_channel: Mapping[str, Iterable[str]] | None = None,
    filters: QueryFilters | Mapping[str, object] | None = None,
) -> RetrievalOutput:
    """Aggregate every channel before any fusion or ordering is performed."""

    candidates: list[CandidateWindowData] = []
    traces: list[ChannelRetrievalTrace] = []
    channel_ids = [channel.channel_id for channel in channels]
    if len(channel_ids) != len(set(channel_ids)):
        raise ValueError("channel IDs must be unique")
    scoped_windows = apply_scope_filters(windows, filters)
    for channel in channels:
        expected = (
            expected_tick_ids_by_channel.get(channel.channel_id)
            if expected_tick_ids_by_channel
            else None
        )
        channel_candidates, trace = aggregate_channel(
            channel, scoped_windows, expected_tick_ids=expected
        )
        candidates.extend(channel_candidates)
        traces.append(trace)
    return RetrievalOutput(
        candidates=tuple(candidates),
        traces=tuple(traces),
        channels_run=tuple(channel_ids),
        exact_scoring_completed=all(trace.exact_scoring_completed for trace in traces),
    )


def _load_input(payload: Mapping[str, object]) -> tuple[list[ChannelScoreInput], list[WindowSpan]]:
    windows = [WindowSpan(**item) for item in payload.get("windows", [])]  # type: ignore[arg-type]
    channels: list[ChannelScoreInput] = []
    for raw in payload.get("channels", []):  # type: ignore[assignment]
        item = dict(raw)
        item["ticks"] = tuple(ScoreTick(**tick) for tick in item.get("ticks", []))
        item["atom_ids"] = tuple(item.get("atom_ids", ()))
        channels.append(ChannelScoreInput(**item))
    return channels, windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate exact channel scores")
    parser.add_argument("input_json")
    args = parser.parse_args(argv)
    with open(args.input_json, encoding="utf-8") as source:
        payload = json.load(source)
    channels, windows = _load_input(payload)
    output = retrieve_channels(channels, windows)
    print(
        json.dumps(
            {
                "candidates": [
                    candidate_window_payload(candidate)
                    for candidate in output.candidates
                ],
                "traces": [trace.__dict__ for trace in output.traces],
                "channels_run": output.channels_run,
                "exact_scoring_completed": output.exact_scoring_completed,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
