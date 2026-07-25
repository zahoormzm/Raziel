from __future__ import annotations

from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from grounding.boxes import (
    GroundingBox,
    evaluate_grounding_gate,
    ground_verified_candidate,
)
from ml.temporal_reranker.checkpoint import (
    CheckpointMetadata,
    CheckpointPayload,
    capture_rng_state,
    load_checkpoint,
    save_checkpoint_atomic,
    validate_resume,
)
from ml.temporal_reranker.dataset import (
    CandidateFeatureRecord,
    DatasetManifest,
    FeatureCacheIdentity,
    RerankerLabels,
    evaluate_training_data_gate,
)
from ml.temporal_reranker.gates import evaluate_ship_gate
from ml.temporal_reranker.feature_cache import FeatureCacheStore
from ml.temporal_reranker.jobs import GPULease, LeaseBusyError, LocalJobRegistry
from packages.contracts.candidates import CandidateCluster, CandidateSet
from packages.contracts.query_plan import Atom, AtomType, QueryPlan
from packages.contracts.verification import (
    ConstraintEvidence,
    EvidenceState,
    VerificationResult,
)
from query.boundaries import (
    BoundaryConfig,
    BoundaryFrame,
    measure_boundary_error,
    refine_boundaries,
)
from query.decide import decide_candidate, derive_archive_conclusion, rank_near_misses
from query.rerank import RerankerResult, rerank_candidates
from query.verify import (
    EvidenceFrame,
    VerificationCache,
    VerificationCacheKey,
    VerificationRequest,
    verify_candidate,
)
from scripts.day1_bench import main as day1_bench_main, summarize


def make_record(
    index: int,
    *,
    split: str = "train",
    challenges: set[str] | None = None,
    absent: bool = False,
) -> CandidateFeatureRecord:
    return CandidateFeatureRecord(
        candidate_id=f"c{index}",
        episode_id=f"episode-{index}",
        query_id=f"q{index // 2}",
        scenario_id=f"scenario-{index}",
        session_id=f"session-{index}",
        split=split,
        source_hash=f"source-{index}",
        candidate_bounds=(0.0, 2.0),
        frame_ids=(index * 10, index * 10 + 1, index * 10 + 2),
        pts=(0.0, 1.0, 2.0),
        sequence_features={
            "query_similarity": (0.1, 0.8, 0.2),
            "relative_time": (0.0, 0.5, 1.0),
            "window_scale": (12.0, 12.0, 12.0),
            "motion": (0.0, 1.0, 0.0),
            "luminance": (0.5, 0.5, 0.5),
            "sharpness": (0.7, 0.7, 0.7),
            "missing_frame": (0.0, 0.0, 0.0),
        },
        atom_features={"a1": (0.1, 0.9, 0.2)},
        relation_features={"r1": (0.0, 0.8, 0.1)},
        challenger_types=frozenset(challenges or ()),
        is_absent_example=absent,
        labels=RerankerLabels(
            relevance=0 if absent else index % 2,
            atom_support={"a1": 0 if absent else 1},
            relation_support={"r1": 1},
            start_index=1,
            end_index=1,
            frame_relevance=(0, 1, 0),
        ),
    )


def make_plan() -> QueryPlan:
    return QueryPlan(
        query_text="a red bag",
        atoms=[
            Atom(atom_id="a1", text_span="bag", type=AtomType.OBJECT),
            Atom(atom_id="a2", text_span="red", type=AtomType.ATTRIBUTE),
        ],
    )


def make_request() -> VerificationRequest:
    frames = tuple(EvidenceFrame(frame_id=index, pts=float(index)) for index in range(8))
    return VerificationRequest(
        candidate_id="candidate-1",
        source_hash="source-hash",
        candidate_bounds=(0.0, 7.0),
        frames=frames,
        subsegments=(),
        query_plan=make_plan(),
        model_revision="qwen-local-revision",
        quantization="4bit",
        prompt_schema_version="verify-v1",
        decoding_parameters={"temperature": 0},
        pipeline_version="pipeline-v1",
        operating_point_config_hash="op-hash",
    )


def ship_metrics() -> dict[str, float | bool]:
    return {
        "baseline_temporal_set_f1": 0.70,
        "reranker_temporal_set_f1": 0.73,
        "baseline_vlm_calls": 10.0,
        "reranker_vlm_calls": 9.0,
        "baseline_candidate_recall": 0.95,
        "reranker_candidate_recall": 0.95,
        "baseline_rejection_f1": 0.80,
        "reranker_rejection_f1": 0.80,
        "baseline_required_macro_f1": 0.70,
        "reranker_required_macro_f1": 0.70,
        "baseline_atom_support_f1": 0.65,
        "reranker_atom_support_f1": 0.66,
        "baseline_complete_set_recall": 0.90,
        "reranker_complete_set_recall": 0.90,
        "reranker_latency_s": 0.4,
        "fallback_exercised": True,
    }


class DatasetGateTests(unittest.TestCase):
    def test_exact_data_gate_passes_complete_dataset(self) -> None:
        challengers = [
            "wrong_attribute",
            "wrong_binding",
            "wrong_order",
            "partial_event",
            "unobservable",
            "track_fragmentation",
            "bounded_or",
            "visible_absence",
            "bounded_count",
        ]
        records = []
        for index in range(150):
            split = "train" if index < 100 else "dev" if index < 125 else "test"
            records.append(
                make_record(
                    index,
                    split=split,
                    challenges={challengers[index % len(challengers)]},
                    absent=index == 0,
                )
            )
        manifest = DatasetManifest(
            schema_version="v1",
            dataset_manifest_hash="dataset",
            split_hash="split",
            feature_cache_version="features-v1",
            model_revision_hashes={"siglip": "revision"},
            independent_episode_count=150,
            confusion_table_ready=True,
        )
        report = evaluate_training_data_gate(
            records, manifest, request_relation_logic_head=True
        )
        self.assertTrue(report.passed, report.blockers)
        self.assertTrue(report.relation_logic_head_allowed)

    def test_data_gate_refuses_small_or_leaking_dataset(self) -> None:
        records = [
            make_record(0, split="train", challenges={"wrong_attribute"}, absent=True),
            replace(
                make_record(1, split="test"),
                scenario_id="scenario-0",
                session_id="session-0",
            ),
        ]
        manifest = DatasetManifest(
            "v1", "dataset", "split", "features", {"encoder": "rev"}, 2, False
        )
        report = evaluate_training_data_gate(
            records, manifest, request_relation_logic_head=True
        )
        self.assertFalse(report.passed)
        self.assertTrue(any("150" in blocker for blocker in report.blockers))
        self.assertTrue(any("leak" in blocker for blocker in report.blockers))

    def test_feature_cache_key_is_complete_and_sensitive(self) -> None:
        identity = FeatureCacheIdentity(
            source_hash="source",
            candidate_bounds=(0.0, 1.0),
            query_plan_hash="query",
            frame_ids_and_pts=((1, 0.0),),
            preprocessing_version="pre",
            feature_schema_version="schema",
            encoder_revision_hashes={"siglip": "rev"},
            graph_schema_version="graph",
        )
        self.assertNotEqual(identity.key, replace(identity, query_plan_hash="other").key)
        self.assertNotEqual(
            identity.key, replace(identity, frame_ids_and_pts=((1, 0.1),)).key
        )

    def test_feature_cache_is_content_addressed_and_round_trips(self) -> None:
        record = make_record(4)
        identity = FeatureCacheIdentity(
            source_hash=record.source_hash,
            candidate_bounds=record.candidate_bounds,
            query_plan_hash="query",
            frame_ids_and_pts=tuple(zip(record.frame_ids, record.pts)),
            preprocessing_version="pre",
            feature_schema_version="schema",
            encoder_revision_hashes={"siglip": "rev"},
            graph_schema_version=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = FeatureCacheStore(directory)
            first_path = store.put(identity, record)
            second_path = store.put(identity, record)
            self.assertEqual(first_path, second_path)
            self.assertEqual(store.get(identity), record)


class GateAndCheckpointTests(unittest.TestCase):
    def test_ship_gate_passes_only_measured_conditions(self) -> None:
        report = evaluate_ship_gate(ship_metrics(), rejection_material_tolerance=0.0)
        self.assertTrue(report.passed, report.blockers)
        unmeasured = evaluate_ship_gate({}, rejection_material_tolerance=None)
        self.assertFalse(unmeasured.passed)
        self.assertEqual(unmeasured.status, "not_measured")

    def _metadata(self, **updates: object) -> CheckpointMetadata:
        values = dict(
            run_id="run-1",
            parent_run_id=None,
            epoch=1,
            global_step=2,
            microbatch_position=0,
            gradient_accumulation_position=0,
            sampler_epoch=1,
            sampler_offset=3,
            resolved_config={"lr": 1e-3},
            command_line=("train.py",),
            git_commit="commit",
            git_dirty=False,
            dataset_manifest_hash="dataset",
            split_hash="split",
            feature_cache_version="features",
            model_revision_hashes={"encoder": "rev"},
            best_metric=0.5,
            early_stopping_state={"wait": 0},
            evaluation_history=({"loss": 0.5},),
            device="cuda",
            precision="fp32",
            created_at="now",
        )
        values.update(updates)
        return CheckpointMetadata(**values)

    def test_checkpoint_round_trip_and_resume_mismatch(self) -> None:
        payload = CheckpointPayload(
            metadata=self._metadata(),
            model_state={"weight": [1]},
            optimizer_state={"state": {}},
            scheduler_state={"epoch": 1},
            amp_scaler_state=None,
            rng_state=capture_rng_state(),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = save_checkpoint_atomic(Path(directory) / "last.ckpt", payload)
            loaded = load_checkpoint(path)
            self.assertEqual(loaded.metadata.run_id, "run-1")
        refused = validate_resume(
            self._metadata(),
            self._metadata(dataset_manifest_hash="changed"),
        )
        self.assertFalse(refused.allowed)
        self.assertIn("dataset_manifest_hash mismatch", refused.blockers)

    def test_changed_precision_requires_child_run(self) -> None:
        child = self._metadata(
            run_id="run-2", parent_run_id="run-1", precision="fp16"
        )
        report = validate_resume(self._metadata(), child)
        self.assertTrue(report.allowed)
        self.assertFalse(report.exact_continuation)

    def test_gpu_lease_and_job_yield_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lease_path = Path(directory) / "gpu.lease"
            first = GPULease(lease_path, mode="train", owner="run-1")
            first.acquire()
            try:
                with self.assertRaises(LeaseBusyError):
                    GPULease(lease_path, mode="serve", owner="interactive").acquire()
            finally:
                first.release()
            registry = LocalJobRegistry(Path(directory) / "jobs.sqlite")
            registry.enqueue(run_id="run-1", mode="train", payload={"x": 1})
            registry.start("run-1")
            registry.request_yield("run-1")
            self.assertTrue(registry.yield_requested("run-1"))
            registry.mark_yielded("run-1", "last.ckpt")
            self.assertEqual(registry.get("run-1")["state"], "queued")


class DeterministicFakeVerifierBackend:
    """Test-only deterministic backend; never selected by production code."""

    def __init__(self, replies: list[object]) -> None:
        self.replies = replies
        self.calls: list[tuple[str | None, tuple[str, ...]]] = []

    def verify(
        self,
        request: VerificationRequest,
        *,
        recovery_instruction: str | None = None,
        target_constraint_ids: tuple[str, ...] | list[str] = (),
    ) -> object:
        self.calls.append((recovery_instruction, tuple(target_constraint_ids)))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def result_payload(
    a1_state: str = "supported",
    a2_state: str = "supported",
    *,
    a1_frame: int = 1,
) -> dict[str, object]:
    return {
        "atoms": [
            {
                "constraint_id": "a1",
                "state": a1_state,
                "reason_code": "visible_match" if a1_state == "supported" else "occlusion",
                "evidence_frame_ids": [a1_frame] if a1_state == "supported" else [],
                "rationale": "bag visible",
            },
            {
                "constraint_id": "a2",
                "state": a2_state,
                "reason_code": "visible_match" if a2_state == "supported" else "low_light",
                "evidence_frame_ids": [2] if a2_state == "supported" else [],
                "rationale": "colour visible",
            },
        ],
        "relations": [],
        "logic_groups": [],
        "matching_subintervals": [{"start_frame_id": 1, "end_frame_id": 2}],
    }


class VerificationAndDecisionTests(unittest.TestCase):
    def test_bad_citation_gets_one_targeted_recovery(self) -> None:
        recovery = {
            "atoms": [
                {
                    "constraint_id": "a1",
                    "state": "supported",
                    "reason_code": "visible_match",
                    "evidence_frame_ids": [1],
                    "rationale": "valid citation",
                }
            ],
            "relations": [],
            "logic_groups": [],
        }
        backend = DeterministicFakeVerifierBackend(
            [result_payload(a1_frame=999), recovery]
        )
        run = verify_candidate(make_request(), backend)
        self.assertTrue(run.targeted_recovery_used)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(backend.calls[1][1], ("a1",))
        self.assertEqual(run.result.atoms[0].state, EvidenceState.SUPPORTED)
        self.assertEqual(run.evidence_pts_by_constraint["a1"], (1.0,))

    def test_invalid_schema_retries_once(self) -> None:
        backend = DeterministicFakeVerifierBackend([{"atoms": []}, result_payload()])
        run = verify_candidate(make_request(), backend)
        self.assertTrue(run.schema_retry_used)
        self.assertEqual(run.result.retry_count, 1)
        self.assertEqual(len(backend.calls), 2)

    def test_timeout_becomes_undetermined_then_recovers_once(self) -> None:
        backend = DeterministicFakeVerifierBackend(
            [TimeoutError(), result_payload()]
        )
        run = verify_candidate(make_request(), backend)
        self.assertTrue(run.targeted_recovery_used)
        self.assertEqual(len(backend.calls), 2)
        self.assertTrue(
            all(item.state == EvidenceState.SUPPORTED for item in run.result.atoms)
        )

    def test_cache_key_and_bypass(self) -> None:
        request = make_request()
        self.assertIn("query_plan", request.to_payload())
        key = VerificationCacheKey.from_request(request)
        changed = VerificationCacheKey.from_request(
            replace(request, quantization="8bit")
        )
        self.assertNotEqual(key.digest, changed.digest)
        with tempfile.TemporaryDirectory() as directory:
            cache = VerificationCache(Path(directory) / "cache.sqlite")
            first_backend = DeterministicFakeVerifierBackend([result_payload()])
            first = verify_candidate(request, first_backend, cache=cache)
            self.assertFalse(first.cache_hit)
            second_backend = DeterministicFakeVerifierBackend([])
            second = verify_candidate(request, second_backend, cache=cache)
            self.assertTrue(second.cache_hit)

    def test_mixed_unresolved_states_preserve_both_dimensions(self) -> None:
        verification = VerificationResult(
            candidate_id="candidate",
            model_revision="model",
            prompt_schema_version="schema",
            atoms=[
                ConstraintEvidence(
                    constraint_id="a1",
                    state=EvidenceState.UNOBSERVABLE,
                    reason_code="occlusion",
                ),
                ConstraintEvidence(
                    constraint_id="a2",
                    state=EvidenceState.UNDETERMINED,
                    reason_code="timeout",
                ),
            ],
        )
        decision = decide_candidate(
            verification, required_constraint_ids={"a1", "a2"}
        )
        self.assertTrue(decision.unresolved_visual)
        self.assertTrue(decision.unresolved_system)
        self.assertFalse(decision.verified_match)
        with self.assertRaisesRegex(ValueError, "shared contract policy"):
            derive_archive_conclusion(
                [decision],
                assembly_complete=True,
                verification_complete=True,
                graph_join_budget_reached=False,
            )

    def test_contradiction_rejects_and_near_miss_sort_is_deterministic(self) -> None:
        verification = VerificationResult(
            candidate_id="candidate",
            model_revision="model",
            prompt_schema_version="schema",
            atoms=[
                ConstraintEvidence(
                    constraint_id="a1",
                    state=EvidenceState.SUPPORTED,
                    reason_code="visible_match",
                    evidence_frame_ids=[1],
                ),
                ConstraintEvidence(
                    constraint_id="a2",
                    state=EvidenceState.CONTRADICTED,
                    reason_code="visible_mismatch",
                    evidence_frame_ids=[2],
                    rationale="bag is blue",
                ),
            ],
        )
        decision = decide_candidate(
            verification, required_constraint_ids={"a1", "a2"}
        )
        near = rank_near_misses(
            [decision],
            verification_by_candidate={"candidate": verification},
            retrieval_priority={"candidate": 2.0},
        )
        self.assertTrue(decision.rejected_near_match)
        self.assertEqual(near[0].contradiction_summary, ("a2: bag is blue",))
        self.assertEqual(
            derive_archive_conclusion(
                [decision],
                assembly_complete=True,
                verification_complete=True,
                graph_join_budget_reached=False,
            ).value,
            "no_verified_match_at_operating_point",
        )


class BoundaryRerankAndGroundingTests(unittest.TestCase):
    def test_boundary_refinement_uses_relevance_and_pts(self) -> None:
        frames = [
            BoundaryFrame(10, 0.0, 0.1),
            BoundaryFrame(11, 0.5, 0.8),
            BoundaryFrame(12, 1.0, 0.9),
            BoundaryFrame(13, 1.5, 0.2),
        ]
        refined = refine_boundaries(
            frames,
            proposed_start_frame_id=10,
            proposed_end_frame_id=13,
            config=BoundaryConfig(0.7, 0.2, 0.3),
            source_duration_s=2.0,
        )
        self.assertEqual((refined.onset_frame_id, refined.offset_frame_id), (11, 12))
        self.assertAlmostEqual(refined.padded_start_pts, 0.3)
        report = measure_boundary_error([refined], [(0.4, 1.2)])
        self.assertAlmostEqual(report.median_combined_error_s, 0.15)

    def test_reranker_is_shadow_until_ship_gate_passes(self) -> None:
        candidate_set = CandidateSet(
            search_id="search",
            query_plan_version="v1",
            exact_scoring_completed=True,
            clusters=[
                CandidateCluster(
                    cluster_id="c1",
                    video_id="video",
                    t0=0,
                    t1=1,
                    member_candidate_ids=["w1"],
                    priority_score=1,
                ),
                CandidateCluster(
                    cluster_id="c2",
                    video_id="video",
                    t0=1,
                    t1=2,
                    member_candidate_ids=["w2"],
                    priority_score=2,
                ),
            ],
        )
        backend = DeterministicFakeRerankerBackend()
        features = {"c1": make_record(1), "c2": make_record(2)}
        features = {
            key: replace(value, candidate_id=key) for key, value in features.items()
        }
        shadow = rerank_candidates(
            candidate_set,
            features=features,
            feature_cache_keys={"c1": "k1", "c2": "k2"},
            atom_ids={"a1"},
            relation_logic_ids={"r1"},
            backend=backend,
            feature_enabled=True,
            ship_gate=None,
        )
        self.assertEqual(shadow.mode, "shadow")
        self.assertEqual(shadow.ordered_cluster_ids, ("c2", "c1"))
        active = rerank_candidates(
            candidate_set,
            features=features,
            feature_cache_keys={"c1": "k1", "c2": "k2"},
            atom_ids={"a1"},
            relation_logic_ids={"r1"},
            backend=backend,
            feature_enabled=True,
            ship_gate=evaluate_ship_gate(
                ship_metrics(), rejection_material_tolerance=0.0
            ),
        )
        self.assertEqual(active.mode, "active")
        self.assertEqual(active.ordered_cluster_ids, ("c1", "c2"))

    def test_grounding_refuses_unverified_and_falls_back_on_unmeasured_gate(self) -> None:
        verification = VerificationResult(
            candidate_id="candidate-1",
            model_revision="model",
            prompt_schema_version="schema",
            atoms=[
                ConstraintEvidence(
                    constraint_id="a1",
                    state=EvidenceState.SUPPORTED,
                    reason_code="visible_match",
                    evidence_frame_ids=[1],
                ),
                ConstraintEvidence(
                    constraint_id="a2",
                    state=EvidenceState.SUPPORTED,
                    reason_code="visible_match",
                    evidence_frame_ids=[2],
                ),
            ],
        )
        decision = decide_candidate(
            verification, required_constraint_ids={"a1", "a2"}
        )
        gate = evaluate_grounding_gate(
            evaluated_instances=None,
            correct_correspondence_instances=None,
            grossly_incorrect_boxes=None,
            overlay_latency_s=None,
            max_overlay_latency_s=None,
            core_verdict_unchanged_when_disabled=None,
        )
        evidence = ground_verified_candidate(
            decision,
            verification,
            make_plan(),
            make_request().frames,
            backend=DeterministicFakeGrounder(),
            feature_enabled=True,
            gate=gate,
        )
        self.assertFalse(evidence.overlay_enabled)
        self.assertEqual(evidence.fallback_reason, "grounding_gate_not_passed")

    def test_grounding_gate_and_candidate_only_overlay(self) -> None:
        verification = VerificationResult(
            candidate_id="candidate-1",
            model_revision="model",
            prompt_schema_version="schema",
            atoms=[
                ConstraintEvidence(
                    constraint_id="a1",
                    state=EvidenceState.SUPPORTED,
                    reason_code="visible_match",
                    evidence_frame_ids=[1],
                ),
                ConstraintEvidence(
                    constraint_id="a2",
                    state=EvidenceState.SUPPORTED,
                    reason_code="visible_match",
                    evidence_frame_ids=[2],
                ),
            ],
        )
        decision = decide_candidate(
            verification, required_constraint_ids={"a1", "a2"}
        )
        gate = evaluate_grounding_gate(
            evaluated_instances=20,
            correct_correspondence_instances=20,
            grossly_incorrect_boxes=0,
            overlay_latency_s=0.5,
            max_overlay_latency_s=1.0,
            core_verdict_unchanged_when_disabled=True,
        )
        evidence = ground_verified_candidate(
            decision,
            verification,
            make_plan(),
            make_request().frames,
            backend=DeterministicFakeGrounder(),
            feature_enabled=True,
            gate=gate,
        )
        self.assertTrue(evidence.overlay_enabled)
        self.assertEqual(len(evidence.boxes), 2)

    def test_b2_hardware_gate_uses_real_measurements_and_cache_assertion(self) -> None:
        rows = [
            {
                "benchmark_id": "B2",
                "phase": "cold",
                "candidate_duration_s": 12.0,
                "latency_s": 25.0,
                "peak_vram_gb": 9.0,
                "total_vram_gb": 12.0,
                "result_cache_hits": 0,
            }
        ]
        rows.extend(
            {
                "benchmark_id": "B2",
                "phase": "warm",
                "candidate_duration_s": 12.0,
                "latency_s": 10.0 + index / 100,
                "peak_vram_gb": 9.5,
                "total_vram_gb": 12.0,
                "result_cache_hits": 0,
            }
            for index in range(20)
        )
        report = summarize(
            "B2",
            rows,
            semantic_gain_b3=False,
            declared_b4_min_rate=None,
            declared_b7_min_fps=None,
        )
        self.assertTrue(report.passed, report.blockers)
        self.assertIsNotNone(report.warm_p95_s)

    def test_day1_bench_creates_output_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "bench" / "report.json"
            exit_code = day1_bench_main(
                [
                    "--environment-only",
                    "--benchmarks",
                    "B2",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output.is_file())


class DeterministicFakeRerankerBackend:
    """Test-only reranker backend."""

    model_revision = "fake-test-reranker-v1"

    def predict(
        self, features: CandidateFeatureRecord, *, feature_cache_key: str
    ) -> RerankerResult:
        score = 10.0 if features.candidate_id == "c1" else 1.0
        return RerankerResult(
            candidate_id=features.candidate_id,
            relevance_score=score,
            atom_support_logits={"a1": 1.0},
            relation_support_logits={"r1": 1.0},
            evidence_frame_relevance=(0.1, 0.8, 0.2),
            start_distribution=(0.1, 0.8, 0.1),
            end_distribution=(0.1, 0.8, 0.1),
            model_revision=self.model_revision,
            feature_cache_key=feature_cache_key,
        )


class DeterministicFakeGrounder:
    """Test-only spatial backend."""

    detector_revision = "fake-test-detector-v1"

    def ground(
        self,
        frame: EvidenceFrame,
        *,
        constraint_id: str,
        phrase: str,
    ) -> list[GroundingBox]:
        return [
            GroundingBox(
                frame_id=frame.frame_id,
                constraint_id=constraint_id,
                phrase=phrase,
                x0=0.1,
                y0=0.1,
                x1=0.5,
                y1=0.5,
                detector_score=0.8,
                detector_revision=self.detector_revision,
            )
        ]


if __name__ == "__main__":
    unittest.main()
