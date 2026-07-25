"""Contract-first local retrieval pipeline.

This module is intentionally useful before a verifier is staged: exact retrieval
runs live and every retained candidate is reported as `undetermined`, never as a
verified match. Thresholds must be supplied by a measured/development operating
point; there is no hidden default.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from index.frame_embed import FrameEmbeddingIndex, FrameTextEncoder, SigLIP2FrameEncoder
from index.stores import ArtifactGeneration, VersionedEmbeddingStore
from packages.contracts.search_result import (
    ArchiveConclusion,
    AssemblySummary,
    CandidateGenerationSummary,
    CandidateOutcome,
    GraphExecutionSummary,
    IndexingSummary,
    SearchResult,
    VerificationSummary,
)
from packages.contracts.verification import (
    ConstraintEvidence,
    EvidenceState,
    VerificationResult,
)
from query.assemble import assemble_temporal
from query.compiler import compile_query
from query.decide import decide_candidate
from query.fuse import (
    cluster_candidates,
    reciprocal_rank_order,
    threshold_then_union,
    union_semantic_and_graph,
)
from query.graph_execute import execute_sqlite
from query.parser import deterministic_parse
from query.retrieve import (
    ChannelScoreInput,
    ScoreTick,
    WindowSpan,
    apply_scope_filters,
    retrieve_channels,
)
from query.schema import InterpretationState, query_plan_payload
from packages.contracts.query_plan import QueryPlan


class LocalRetrievalPipeline:
    def __init__(
        self,
        *,
        database: str | Path,
        frame_store: str | Path,
        model_path: str | Path | None = None,
        model_revision: str | None = None,
        thresholds: Mapping[str, float],
        aggregation: str = "top20_mean",
        window_scales: tuple[int, ...] = (4, 12, 30),
        graph_enabled: bool = False,
        verification_budget_clusters: int = 12,
        operating_point_label: str = "development",
        encoder: FrameTextEncoder | None = None,
        device: str = "cuda",
        candidate_verifier: Callable[[Any, QueryPlan], VerificationResult] | None = None,
    ) -> None:
        required_thresholds = {"whole_query_frame", "candidate_anchor_frame", "rare_attribute_frame"}
        missing = required_thresholds.difference(thresholds)
        if missing:
            raise ValueError(f"operating point is missing thresholds: {sorted(missing)}")
        if any(not -1.0 <= float(value) <= 1.0 for value in thresholds.values()):
            raise ValueError("cosine thresholds must be within [-1, 1]")
        if verification_budget_clusters < 0:
            raise ValueError("verification budget cannot be negative")
        self.database_path = Path(database).resolve()
        self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        store_path = Path(frame_store).resolve()
        metadata = json.loads((store_path / "metadata.json").read_text(encoding="utf-8"))
        generation = ArtifactGeneration(
            schema_version=metadata["schema_version"],
            kind=metadata["kind"],
            inputs=metadata["generation_inputs"],
        )
        store = VersionedEmbeddingStore(
            store_path,
            dimension=int(metadata["dimension"]),
            generation=generation,
            create=False,
        )
        if encoder is None:
            if model_path is None or not model_revision:
                raise ValueError(
                    "model_path and exact model_revision are required when encoder is not supplied"
                )
            encoder = SigLIP2FrameEncoder(
                str(Path(model_path).resolve()),
                revision=model_revision,
                device=device,
                local_files_only=True,
            )
        self.encoder = encoder
        self.frame_index = FrameEmbeddingIndex(self.connection, store)
        self.thresholds = {key: float(value) for key, value in thresholds.items()}
        self.aggregation = aggregation
        self.window_scales = window_scales
        self.graph_enabled = graph_enabled
        self.verification_budget_clusters = verification_budget_clusters
        self.operating_point_label = operating_point_label
        self.candidate_verifier = candidate_verifier
        self._results: dict[str, SearchResult] = {}
        self._lock = threading.RLock()

    def close(self) -> None:
        self.connection.close()

    def parse(self, request: Mapping[str, Any]) -> QueryPlan:
        text = str(request.get("text", "")).strip()
        filters = {
            "video_ids": tuple(request.get("video_ids") or ()),
            "camera_ids": tuple(request.get("camera_ids") or ()),
            "start_time": _optional_float(request.get("start_time")),
            "end_time": _optional_float(request.get("end_time")),
            "zone_ids": tuple(request.get("zone_ids") or ()),
        }
        return QueryPlan.model_validate(query_plan_payload(deterministic_parse(text, filters)))

    def query(self, request: Mapping[str, Any]) -> SearchResult:
        with self._lock:
            plan_data = deterministic_parse(
                str(request.get("text", "")).strip(),
                {
                    "video_ids": tuple(request.get("video_ids") or ()),
                    "camera_ids": tuple(request.get("camera_ids") or ()),
                    "start_time": _optional_float(request.get("start_time")),
                    "end_time": _optional_float(request.get("end_time")),
                    "zone_ids": tuple(request.get("zone_ids") or ()),
                },
            )
            plan = QueryPlan.model_validate(query_plan_payload(plan_data))
            search_id = uuid4().hex
            if plan_data.interpretation_state is InterpretationState.CLARIFICATION_REQUIRED:
                result = self._clarification_result(search_id, plan)
                self._results[search_id] = result
                return result

            compiled = compile_query(
                plan_data,
                enable_graph=self.graph_enabled,
                enable_clip=False,
                enable_ocr=False,
                enable_asr=False,
            )
            scope = {
                "video_ids": list(plan.filters.video_ids),
                "camera_ids": list(plan.filters.camera_ids),
                "t0": plan.filters.start_time,
                "t1": plan.filters.end_time,
            }
            score_channels: list[ChannelScoreInput] = []
            expected_by_channel: dict[str, tuple[str, ...]] = {}
            for channel in compiled.semantic_channels:
                if not channel.enabled or not channel.channel_id.startswith("frame:"):
                    continue
                threshold = self.thresholds.get(channel.threshold_key)
                if threshold is None:
                    raise ValueError(
                        f"no measured threshold for channel kind {channel.threshold_key!r}"
                    )
                ticks, scores = self.frame_index.exact_score_text(
                    channel.text,
                    self.encoder,
                    **scope,
                )
                scored = tuple(
                    ScoreTick(
                        tick_id=f"{tick.video_id}:{tick.target_pts:.6f}:{tick.sampling_lane}",
                        frame_id=tick.frame_id,
                        video_id=tick.video_id,
                        camera_id=tick.camera_id,
                        pts=tick.actual_pts,
                        score=score,
                        producer_version=f"siglip2:{self.encoder.revision}",
                    )
                    for tick, score in zip(ticks, scores.scores)
                )
                score_channels.append(
                    ChannelScoreInput(
                        channel_id=channel.channel_id,
                        ticks=scored,
                        threshold=threshold,
                        aggregation=self.aggregation,
                        atom_ids=channel.atom_ids,
                    )
                )
                expected_by_channel[channel.channel_id] = tuple(item.tick_id for item in scored)

            windows = self._windows(plan)
            retrieved = retrieve_channels(
                tuple(score_channels),
                windows,
                expected_tick_ids_by_channel=expected_by_channel,
            )
            graph_result = None
            if self.graph_enabled:
                graph_result = execute_sqlite(
                    self.connection,
                    compiled.graph_pattern,
                    node_scan_budget=compiled.graph_pattern.join_budget,
                    edge_scan_budget=compiled.graph_pattern.join_budget,
                )
                qualified = union_semantic_and_graph(
                    retrieved.candidates,
                    graph_result.candidates,
                )
            else:
                qualified = threshold_then_union(retrieved.candidates)
            ordered = reciprocal_rank_order(qualified)

            if plan_data.temporal_relations:
                anchor_ids = {
                    relation.first_atom for relation in plan_data.temporal_relations
                }.union(
                    relation.second_atom for relation in plan_data.temporal_relations
                )
                anchors = {
                    atom_id: tuple(item for item in ordered if atom_id in item.atom_ids)
                    for atom_id in anchor_ids
                }
                assembled = assemble_temporal(
                    plan_data,
                    anchors,
                    max_episode_count=int(request.get("max_episode_count", 256)),
                )
                candidate_windows = tuple(item.episode for item in assembled.episodes)
                assembly = AssemblySummary(
                    anchor_candidates_qualifying=assembled.completeness.anchor_candidates_qualifying,
                    anchor_candidates_retained=assembled.completeness.anchor_candidates_retained,
                    episodes_generated=len(candidate_windows),
                    assembly_complete=assembled.completeness.assembly_complete,
                )
            else:
                candidate_windows = ordered
                assembly = AssemblySummary(
                    anchor_candidates_qualifying=len(ordered),
                    anchor_candidates_retained=len(ordered),
                    episodes_generated=len(ordered),
                    assembly_complete=True,
                )
            clusters = cluster_candidates(candidate_windows)
            lanes_by_candidate = {
                candidate.candidate_id: tuple(candidate.qualifying_channels)
                for candidate in candidate_windows
            }
            coverage = self._coverage(plan)
            exact_scoring_complete = (
                bool(score_channels)
                and retrieved.exact_scoring_completed
                and coverage.scored_coverage == 1.0
            )
            (
                verified_matches,
                unresolved_visual,
                unresolved_system,
                rejected_near_misses,
                verification,
                conclusion,
            ) = self._verify_clusters(
                clusters,
                plan,
                lanes_by_candidate,
                graph_edge_ids=(
                    graph_result.trace.edge_ids if graph_result is not None else ()
                ),
                search_complete=(
                    assembly.assembly_complete
                    and exact_scoring_complete
                    and not (
                        graph_result is not None
                        and graph_result.trace.join_budget_reached
                    )
                ),
            )
            result = SearchResult(
                search_id=search_id,
                interpretation=plan,
                scope={
                    "video_ids": list(plan.filters.video_ids),
                    "camera_ids": list(plan.filters.camera_ids),
                    "start_time": plan.filters.start_time,
                    "end_time": plan.filters.end_time,
                    "sampling_policy_version": self._sampling_policy_version(),
                },
                indexing=coverage,
                candidate_generation=CandidateGenerationSummary(
                    channels_run=[
                        *retrieved.channels_run,
                        *(("graph:pattern",) if graph_result is not None else ()),
                    ],
                    qualifying_windows=len(ordered),
                    candidate_recall_operating_point=self.operating_point_label,
                    exact_scoring_completed=exact_scoring_complete,
                ),
                graph_execution=(
                    GraphExecutionSummary(
                        enabled=True,
                        pattern_id=graph_result.trace.pattern_id,
                        tracklets_considered=graph_result.trace.tracklets_considered,
                        joins_completed=graph_result.trace.joins_completed,
                        join_budget_reached=graph_result.trace.join_budget_reached,
                        observation_scope_assessable=(
                            graph_result.trace.observation_scope_assessable
                        ),
                        node_ids=list(graph_result.trace.node_ids),
                        edge_ids=list(graph_result.trace.edge_ids),
                        unresolved_reason=graph_result.trace.unresolved_reason,
                    )
                    if graph_result is not None
                    else GraphExecutionSummary(enabled=False)
                ),
                assembly=assembly,
                verification=verification,
                verified_matches=verified_matches,
                unresolved_visual=unresolved_visual,
                unresolved_system=unresolved_system,
                rejected_near_misses=rejected_near_misses,
                archive_conclusion=conclusion,
            )
            self._results[search_id] = result
            return result

    def _windows(self, plan: QueryPlan) -> tuple[WindowSpan, ...]:
        clauses = ["scale_s IN (" + ",".join("?" for _ in self.window_scales) + ")"]
        params: list[Any] = list(self.window_scales)
        if plan.filters.video_ids:
            clauses.append("w.video_id IN (" + ",".join("?" for _ in plan.filters.video_ids) + ")")
            params.extend(plan.filters.video_ids)
        if plan.filters.camera_ids:
            clauses.append("v.camera_id IN (" + ",".join("?" for _ in plan.filters.camera_ids) + ")")
            params.extend(plan.filters.camera_ids)
        rows = self.connection.execute(
            f"""
            SELECT w.window_id,w.video_id,v.camera_id,w.t0,w.t1
            FROM windows w JOIN videos v ON v.video_id=w.video_id
            WHERE {' AND '.join(clauses)}
            ORDER BY w.video_id,w.t0,w.scale_s
            """,
            params,
        ).fetchall()
        spans = tuple(
            WindowSpan(
                window_id=str(row["window_id"]),
                video_id=row["video_id"],
                camera_id=row["camera_id"],
                t0=max(
                    float(row["t0"]),
                    plan.filters.start_time
                    if plan.filters.start_time is not None
                    else float(row["t0"]),
                ),
                t1=min(
                    float(row["t1"]),
                    plan.filters.end_time
                    if plan.filters.end_time is not None
                    else float(row["t1"]),
                ),
            )
            for row in rows
            if (
                plan.filters.end_time is None
                or float(row["t0"]) < plan.filters.end_time
            )
            and (
                plan.filters.start_time is None
                or float(row["t1"]) > plan.filters.start_time
            )
        )
        return apply_scope_filters(spans, plan.filters)

    def _coverage(self, plan: QueryPlan) -> IndexingSummary:
        clauses = ["1=1"]
        params: list[Any] = []
        if plan.filters.video_ids:
            clauses.append("t.video_id IN (" + ",".join("?" for _ in plan.filters.video_ids) + ")")
            params.extend(plan.filters.video_ids)
        if plan.filters.camera_ids:
            clauses.append("v.camera_id IN (" + ",".join("?" for _ in plan.filters.camera_ids) + ")")
            params.extend(plan.filters.camera_ids)
        if plan.filters.start_time is not None:
            clauses.append("t.target_pts>=?")
            params.append(plan.filters.start_time)
        if plan.filters.end_time is not None:
            clauses.append("t.target_pts<?")
            params.append(plan.filters.end_time)
        row = self.connection.execute(
            f"""
            SELECT count(*) expected,
                   sum(CASE WHEN t.status='embedded' THEN 1 ELSE 0 END) embedded,
                   sum(CASE WHEN t.status='decode_failed' THEN 1 ELSE 0 END) failed,
                   sum(CASE WHEN t.status='skipped' THEN 1 ELSE 0 END) skipped
            FROM sample_ticks t JOIN videos v ON v.video_id=t.video_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchone()
        expected = int(row["expected"] or 0)
        embedded = int(row["embedded"] or 0)
        return IndexingSummary(
            expected_ticks=expected,
            embedded_ticks=embedded,
            decode_failed_ticks=int(row["failed"] or 0),
            skipped_ticks=int(row["skipped"] or 0),
            scored_coverage=embedded / expected if expected else 0.0,
        )

    def _unverified_outcome(
        self,
        cluster: Any,
        plan: QueryPlan,
        lanes_by_candidate: Mapping[str, tuple[str, ...]],
        graph_edge_ids: tuple[str, ...] = (),
        reason_code: str = "model_error",
        rationale: str = "The verifier is not configured; retrieval evidence is not a verified match.",
    ) -> CandidateOutcome:
        constraints = [
            ConstraintEvidence(
                constraint_id=constraint_id,
                state=EvidenceState.UNDETERMINED,
                reason_code=reason_code,
                evidence_frame_ids=[],
                rationale=rationale,
            )
            for constraint_id in [
                *(atom.atom_id for atom in plan.atoms if atom.required),
                *(relation.relation_id for relation in plan.relations if relation.required),
                *(group.group_id for group in plan.logic_groups),
            ]
        ]
        lanes = sorted(
            {
                lane
                for candidate_id in cluster.member_candidate_ids
                for lane in lanes_by_candidate.get(candidate_id, ())
            }
        )
        return CandidateOutcome(
            candidate_id=cluster.cluster_id,
            video_id=cluster.video_id,
            camera_id=cluster.camera_id,
            t0=cluster.t0,
            t1=cluster.t1,
            constraints=constraints,
            rationale=rationale,
            retrieval_lanes=lanes,
            graph_node_ids=(
                [evidence.node_id for evidence in cluster.evidence]
                if "graph:pattern" in lanes
                else []
            ),
            graph_edge_ids=(
                list(graph_edge_ids) if "graph:pattern" in lanes else []
            ),
            preview_url=f"/media/{cluster.video_id}#t={cluster.t0:.3f},{cluster.t1:.3f}",
            verification_cached=False,
        )

    def _verify_clusters(
        self,
        clusters: tuple[Any, ...],
        plan: QueryPlan,
        lanes_by_candidate: Mapping[str, tuple[str, ...]],
        *,
        graph_edge_ids: tuple[str, ...],
        search_complete: bool,
    ) -> tuple[
        list[CandidateOutcome],
        list[CandidateOutcome],
        list[CandidateOutcome],
        list[CandidateOutcome],
        VerificationSummary,
        ArchiveConclusion,
    ]:
        if self.candidate_verifier is None:
            unresolved = [
                self._unverified_outcome(
                    cluster,
                    plan,
                    lanes_by_candidate,
                    graph_edge_ids=graph_edge_ids,
                )
                for cluster in clusters
            ]
            conclusion = (
                ArchiveConclusion.SEARCH_INCOMPLETE
                if unresolved or not search_complete
                else ArchiveConclusion.NO_VERIFIED_MATCH
            )
            return (
                [],
                [],
                unresolved,
                [],
                VerificationSummary(
                    state="system_failure" if unresolved else "complete",
                    clusters_total=len(clusters),
                    clusters_verified=0,
                    seconds_used=0.0,
                ),
                conclusion,
            )

        required_ids = {
            *(atom.atom_id for atom in plan.atoms if atom.required),
            *(relation.relation_id for relation in plan.relations if relation.required),
            *(group.group_id for group in plan.logic_groups),
        }
        verified_matches: list[CandidateOutcome] = []
        unresolved_visual: list[CandidateOutcome] = []
        unresolved_system: list[CandidateOutcome] = []
        rejected_near_misses: list[CandidateOutcome] = []
        verified_count = 0
        cache_hits = 0
        system_failure = False
        verification_started = time.perf_counter()
        within_budget = clusters[: self.verification_budget_clusters]
        for cluster in within_budget:
            try:
                result = self.candidate_verifier(cluster, plan)
                if result.candidate_id != cluster.cluster_id:
                    raise ValueError("verifier returned a different candidate ID")
                decision = decide_candidate(
                    result,
                    required_constraint_ids=required_ids,
                )
                outcome = self._verified_outcome(
                    cluster,
                    result,
                    lanes_by_candidate,
                    graph_edge_ids=graph_edge_ids,
                )
                verified_count += 1
                cache_hits += int(result.cached)
            except Exception as exc:
                system_failure = True
                unresolved_system.append(
                    self._unverified_outcome(
                        cluster,
                        plan,
                        lanes_by_candidate,
                        graph_edge_ids=graph_edge_ids,
                        rationale=f"Verifier failure: {type(exc).__name__}.",
                    )
                )
                continue
            if decision.verified_match:
                verified_matches.append(outcome)
            elif decision.rejected_near_match:
                rejected_near_misses.append(outcome)
            else:
                if decision.unresolved_visual:
                    unresolved_visual.append(outcome)
                if decision.unresolved_system:
                    unresolved_system.append(outcome)

        budget_reached = len(within_budget) < len(clusters)
        for cluster in clusters[len(within_budget) :]:
            unresolved_system.append(
                self._unverified_outcome(
                    cluster,
                    plan,
                    lanes_by_candidate,
                    graph_edge_ids=graph_edge_ids,
                    reason_code="timeout",
                    rationale="Not assessed before the declared verification budget was reached.",
                )
            )
        verification_state = (
            "system_failure"
            if system_failure
            else "budget_reached"
            if budget_reached
            else "complete"
        )
        incomplete = not search_complete or verification_state != "complete"
        if verified_matches:
            conclusion = ArchiveConclusion.VERIFIED_MATCHES_FOUND
        elif incomplete:
            conclusion = ArchiveConclusion.SEARCH_INCOMPLETE
        elif unresolved_visual and unresolved_system:
            raise ValueError(
                "mixed unobservable/undetermined candidates require the pending shared reporting policy"
            )
        elif unresolved_visual:
            conclusion = ArchiveConclusion.INSUFFICIENT_VISUAL_EVIDENCE
        elif unresolved_system:
            conclusion = ArchiveConclusion.NOT_APPLICABLE
        else:
            conclusion = ArchiveConclusion.NO_VERIFIED_MATCH
        return (
            verified_matches,
            unresolved_visual,
            unresolved_system,
            rejected_near_misses,
            VerificationSummary(
                state=verification_state,
                clusters_total=len(clusters),
                clusters_verified=verified_count,
                seconds_used=time.perf_counter() - verification_started,
                cache_hits=cache_hits,
            ),
            conclusion,
        )

    def _verified_outcome(
        self,
        cluster: Any,
        verification: VerificationResult,
        lanes_by_candidate: Mapping[str, tuple[str, ...]],
        *,
        graph_edge_ids: tuple[str, ...],
    ) -> CandidateOutcome:
        lanes = sorted(
            {
                lane
                for candidate_id in cluster.member_candidate_ids
                for lane in lanes_by_candidate.get(candidate_id, ())
            }
        )
        return CandidateOutcome(
            candidate_id=cluster.cluster_id,
            video_id=cluster.video_id,
            camera_id=cluster.camera_id,
            t0=cluster.t0,
            t1=cluster.t1,
            constraints=[
                *verification.atoms,
                *verification.relations,
                *verification.logic_groups,
            ],
            rationale="Structured verifier result.",
            retrieval_lanes=lanes,
            graph_node_ids=(
                [evidence.node_id for evidence in cluster.evidence]
                if "graph:pattern" in lanes
                else []
            ),
            graph_edge_ids=(
                list(graph_edge_ids) if "graph:pattern" in lanes else []
            ),
            preview_url=f"/media/{cluster.video_id}#t={cluster.t0:.3f},{cluster.t1:.3f}",
        )

    def media_path(self, video_id: str) -> Path | None:
        row = self.connection.execute(
            "SELECT path FROM videos WHERE video_id=?",
            (video_id,),
        ).fetchone()
        if row is None:
            return None
        path = Path(str(row["path"])).resolve()
        return path if path.is_file() else None

    def result(self, search_id: str) -> SearchResult | None:
        return self._results.get(search_id)

    def coverage_report(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT v.video_id,v.camera_id,v.duration_s,v.pipeline_version,
                   count(t.target_pts) expected_ticks,
                   sum(CASE WHEN t.status='embedded' THEN 1 ELSE 0 END) embedded_ticks,
                   sum(CASE WHEN t.status='decode_failed' THEN 1 ELSE 0 END) decode_failed_ticks,
                   sum(CASE WHEN t.status='skipped' THEN 1 ELSE 0 END) skipped_ticks
            FROM videos v
            LEFT JOIN sample_ticks t ON t.video_id=v.video_id
            GROUP BY v.video_id,v.camera_id,v.duration_s,v.pipeline_version
            ORDER BY v.video_id
            """
        ).fetchall()
        sources = []
        for row in rows:
            expected = int(row["expected_ticks"] or 0)
            embedded = int(row["embedded_ticks"] or 0)
            sources.append(
                {
                    "video_id": row["video_id"],
                    "camera_id": row["camera_id"],
                    "duration_s": float(row["duration_s"] or 0.0),
                    "sampling_policy_version": row["pipeline_version"] or "unknown",
                    "expected_ticks": expected,
                    "embedded_ticks": embedded,
                    "decode_failed_ticks": int(row["decode_failed_ticks"] or 0),
                    "skipped_ticks": int(row["skipped_ticks"] or 0),
                    "scored_coverage": embedded / expected if expected else 0.0,
                }
            )
        return {"sources": sources, "source_count": len(sources)}

    def _sampling_policy_version(self) -> str:
        values = {
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT pipeline_version FROM videos"
            ).fetchall()
        }
        return ",".join(sorted(value for value in values if value)) or "unknown"

    def _clarification_result(self, search_id: str, plan: QueryPlan) -> SearchResult:
        return SearchResult(
            search_id=search_id,
            interpretation=plan,
            scope={
                "video_ids": list(plan.filters.video_ids),
                "camera_ids": list(plan.filters.camera_ids),
                "start_time": plan.filters.start_time,
                "end_time": plan.filters.end_time,
                "sampling_policy_version": self._sampling_policy_version(),
            },
            indexing=self._coverage(plan),
            candidate_generation=CandidateGenerationSummary(
                channels_run=[],
                qualifying_windows=0,
                candidate_recall_operating_point=self.operating_point_label,
                exact_scoring_completed=False,
            ),
            graph_execution=GraphExecutionSummary(enabled=False),
            assembly=AssemblySummary(),
            verification=VerificationSummary(
                state="complete",
                clusters_total=0,
                clusters_verified=0,
            ),
            archive_conclusion=ArchiveConclusion.NOT_APPLICABLE,
        )


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
