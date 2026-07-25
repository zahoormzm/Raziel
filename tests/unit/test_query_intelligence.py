from __future__ import annotations

import sqlite3
import unittest

from evidence.predicates import (
    ObservationSafety,
    PredicateState,
    TrackletObservation,
    evaluate_bounded_count,
    evaluate_visible_none,
)
from packages.contracts.candidates import CandidateSet
from packages.contracts.query_plan import GraphPattern as ContractGraphPattern
from packages.contracts.query_plan import QueryPlan as ContractQueryPlan
from query.assemble import assemble_temporal
from query.compiler import compile_query, graph_pattern_payload
from query.fuse import (
    candidate_set_payload,
    cluster_candidates,
    fusion_evaluation_payload,
    reciprocal_rank_order,
    threshold_then_union,
)
from query.graph_execute import (
    EvidenceEdge,
    EvidenceNode,
    execute_pattern,
    execute_sqlite,
    graph_pattern_from_payload,
    graph_trace_payload,
)
from query.parser import deterministic_parse, parse_query
from query.retrieve import (
    ChannelScoreInput,
    ScoreTick,
    WindowSpan,
    aggregate_values,
    retrieve_channels,
    apply_scope_filters,
    retrieval_evaluation_payload,
)
from query.schema import (
    CandidateWindowData,
    EdgeConstraint,
    GraphPattern,
    InterpretationState,
    LogicConstraint,
    LogicOperator,
    NodeConstraint,
    PredicateState,
    QueryValidationError,
    TemporalRelation,
    query_plan_payload,
)


def candidate(
    candidate_id: str,
    t0: float,
    t1: float,
    *,
    channel: str = "frame:whole",
    score: float = 0.8,
    camera: str | None = "cam-1",
    atom_ids: tuple[str, ...] = (),
    tracklets: tuple[str, ...] = (),
) -> CandidateWindowData:
    return CandidateWindowData(
        candidate_id=candidate_id,
        video_id="video-1",
        camera_id=camera,
        t0=t0,
        t1=t1,
        channel_scores={channel: score},
        qualifying_channels=(channel,),
        atom_ids=atom_ids,
        tracklet_ids=tracklets,
    )


class ParserCompilerTests(unittest.TestCase):
    def test_query_plan_contract_adapter(self) -> None:
        plan = deterministic_parse("A person carries a black bag near the gate")
        self.assertEqual(
            query_plan_payload(plan),
            query_plan_payload(
                deterministic_parse("A person carries a black bag near the gate")
            ),
        )
        contract = ContractQueryPlan.model_validate(query_plan_payload(plan))
        self.assertEqual(contract.state, "clear")
        self.assertTrue(contract.atoms)
        self.assertTrue(contract.relations)

    def test_model_validation_retries_once_then_falls_back(self) -> None:
        calls: list[str | None] = []

        def broken(_text: str, validation_error: str | None = None):
            calls.append(validation_error)
            return {"query_text": "x", "atoms": [{"atom_id": "", "type": "object"}]}

        plan = parse_query("red vehicle", model_parser=broken)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIsNotNone(calls[1])
        self.assertEqual(plan.interpretation_state, InterpretationState.PARSER_FALLBACK)
        self.assertEqual(plan.parser_version, "deterministic-fallback-v1")
        self.assertEqual(plan.filters.camera_ids, ())

    def test_unsupported_language_routes_to_focused_clarification(self) -> None:
        plan = deterministic_parse("Show everyone except the guard")
        self.assertEqual(
            plan.interpretation_state, InterpretationState.CLARIFICATION_REQUIRED
        )
        self.assertTrue(plan.unsupported_constructs)
        self.assertTrue(plan.clarification_question)
        negation = deterministic_parse("person not carrying a bag")
        self.assertEqual(
            negation.interpretation_state,
            InterpretationState.CLARIFICATION_REQUIRED,
        )
        named = deterministic_parse("Find John near the gate")
        self.assertEqual(
            named.interpretation_state,
            InterpretationState.CLARIFICATION_REQUIRED,
        )

    def test_maybe_is_ambiguity_not_optional_atom(self) -> None:
        plan = deterministic_parse("maybe a red vehicle")
        self.assertTrue(plan.ambiguities)
        self.assertTrue(all(atom.required for atom in plan.atoms))

    def test_bounded_logic_parse(self) -> None:
        any_plan = deterministic_parse("a red car or blue van")
        self.assertEqual(any_plan.logic_groups[0].operator, LogicOperator.ANY)
        self.assertEqual(len(any_plan.logic_groups[0].atom_ids), 2)
        three_way = deterministic_parse(
            "red car or blue van or green truck"
        )
        self.assertEqual(len(three_way.logic_groups[0].atom_ids), 3)

        none_plan = deterministic_parse(
            "no person is visible",
            {"camera_ids": ["cam-1"], "start_time": 10, "end_time": 20},
        )
        self.assertEqual(none_plan.logic_groups[0].operator, LogicOperator.VISIBLE_NONE)
        target = none_plan.logic_groups[0].atom_ids[0]
        self.assertEqual(
            next(atom for atom in none_plan.atoms if atom.atom_id == target).role.value,
            "verifier_only",
        )
        terse_none = deterministic_parse(
            "no red cars near the gate",
            {"camera_ids": ["cam-1"], "start_time": 10, "end_time": 20},
        )
        self.assertEqual(
            terse_none.logic_groups[0].operator, LogicOperator.VISIBLE_NONE
        )
        unbounded_none = deterministic_parse("no person is visible")
        self.assertEqual(
            unbounded_none.interpretation_state,
            InterpretationState.CLARIFICATION_REQUIRED,
        )

        count_plan = deterministic_parse(
            "exactly three people",
            {"camera_ids": ["cam-1"], "start_time": 10, "end_time": 20},
        )
        self.assertEqual(count_plan.logic_groups[0].min_count, 3)
        self.assertEqual(count_plan.logic_groups[0].max_count, 3)

    def test_compile_fixed_pattern_and_contract(self) -> None:
        plan = deterministic_parse("person carries bag before walks away")
        compiled = compile_query(plan, join_budget=77, enable_clip=True)
        payload = graph_pattern_payload(compiled)
        contract = ContractGraphPattern.model_validate(payload)
        self.assertEqual(contract.join_budget, 77)
        self.assertNotIn("sql", str(payload).casefold())
        self.assertTrue(compiled.semantic_channels)
        self.assertEqual(
            len({channel.channel_id for channel in compiled.semantic_channels}),
            len(compiled.semantic_channels),
        )
        round_trip = graph_pattern_from_payload(contract)
        self.assertEqual(round_trip.pattern_id, compiled.graph_pattern.pattern_id)
        self.assertEqual(round_trip.join_budget, 77)
        self.assertEqual(
            round_trip.temporal_relations,
            compiled.graph_pattern.temporal_relations,
        )

    def test_temporal_atoms_follow_request_order(self) -> None:
        plan = deterministic_parse("person exits then enters")
        relation = plan.temporal_relations[0]
        by_id = {atom.atom_id: atom.text_span for atom in plan.atoms}
        self.assertEqual(by_id[relation.first_atom], "exits")
        self.assertEqual(by_id[relation.second_atom], "enters")
        same_actor = deterministic_parse(
            "the same person enters then exits"
        ).temporal_relations[0]
        self.assertTrue(same_actor.same_actor_required)
        self.assertEqual(same_actor.max_gap_s, 30)

    def test_compiler_refuses_clarification_plan(self) -> None:
        with self.assertRaises(QueryValidationError):
            compile_query(deterministic_parse("same person hours later"))


class PredicateTests(unittest.TestCase):
    def test_visible_none_requires_complete_assessable_scope(self) -> None:
        incomplete = evaluate_visible_none(
            target_evidence_ids=(),
            safety=ObservationSafety(10, 9, 9),
        )
        self.assertEqual(incomplete.state, PredicateState.UNDETERMINED)
        dark = evaluate_visible_none(
            target_evidence_ids=(),
            safety=ObservationSafety(10, 10, 0, low_light_ticks=10),
        )
        self.assertEqual(dark.state, PredicateState.UNOBSERVABLE)
        absent = evaluate_visible_none(
            target_evidence_ids=(),
            safety=ObservationSafety(10, 10, 10),
        )
        self.assertEqual(absent.state, PredicateState.SUPPORTED)

    def test_count_fragmentation_is_undetermined(self) -> None:
        tracklets = (
            TrackletObservation(
                "t1", "person", "v", "c", 0, 5, continuity="interrupted"
            ),
            TrackletObservation("t2", "person", "v", "c", 5, 10),
        )
        result = evaluate_bounded_count(
            tracklets=tracklets,
            target_label="person",
            min_count=2,
            max_count=2,
            safety=ObservationSafety(10, 10, 10),
        )
        self.assertEqual(result.state, PredicateState.UNDETERMINED)
        self.assertEqual(result.reason_code, "excessive_track_fragmentation")


class RetrievalFusionTests(unittest.TestCase):
    def test_aggregation_and_exact_vector(self) -> None:
        self.assertEqual(aggregate_values([0.1, 0.2, 0.9, 0.8, 0.3]), 0.9)
        ticks = tuple(
            ScoreTick(f"t{i}", i, "v", "c", float(i), score)
            for i, score in enumerate((0.1, 0.2, 0.9, 0.8))
        )
        output = retrieve_channels(
            (
                ChannelScoreInput(
                    "frame:atom:a1",
                    ticks,
                    threshold=0.75,
                    aggregation="max",
                    atom_ids=("a1",),
                ),
            ),
            (WindowSpan("w1", "v", "c", 0, 4),),
            expected_tick_ids_by_channel={
                "frame:atom:a1": ("t0", "t1", "t2", "t3")
            },
        )
        self.assertTrue(output.exact_scoring_completed)
        self.assertEqual(len(output.candidates), 1)
        evaluation = retrieval_evaluation_payload(output)
        self.assertEqual(evaluation["qualifying_windows"], 1)
        self.assertEqual(
            evaluation["channel_traces"][0]["aggregation"],  # type: ignore[index]
            "max",
        )
        scoped = apply_scope_filters(
            (
                WindowSpan("w1", "v", "c", 0, 4),
                WindowSpan("w2", "v", "other", 0, 4),
            ),
            {"camera_ids": ["c"]},
        )
        self.assertEqual([item.window_id for item in scoped], ["w1"])

    def test_threshold_union_rrf_never_removes(self) -> None:
        items = (
            candidate("w1", 0, 4, channel="a", score=0.9),
            candidate("w1", 0, 4, channel="b", score=0.8),
            candidate("w2", 20, 24, channel="b", score=0.7),
            candidate("w3", 40, 44, channel="c", score=0.6),
        )
        union = threshold_then_union(items, thresholds={"a": 0.5, "b": 0.5, "c": 0.5})
        self.assertEqual({item.candidate_id for item in union}, {"w1", "w2", "w3"})
        self.assertEqual(set(union[0].qualifying_channels), {"a", "b"})
        ordered = reciprocal_rank_order(union)
        self.assertEqual(
            {item.candidate_id for item in ordered}, {"w1", "w2", "w3"}
        )
        clusters = cluster_candidates(ordered)
        self.assertEqual(
            {
                member
                for cluster in clusters
                for member in cluster.member_candidate_ids
            },
            {"w1", "w2", "w3"},
        )
        hooks = fusion_evaluation_payload(
            ordered, clusters, verification_budget_clusters=1
        )
        self.assertEqual(hooks["clusters_within_budget"], 1)

    def test_candidate_set_contract(self) -> None:
        windows = reciprocal_rank_order((candidate("w1", 0, 4),))
        clusters = cluster_candidates(windows)
        payload = candidate_set_payload(
            search_id="s1",
            query_plan_version="1",
            channels_run=["frame:whole"],
            exact_scoring_completed=True,
            windows=windows,
            clusters=clusters,
        )
        contract = CandidateSet.model_validate(payload)
        self.assertEqual(len(contract.windows), 1)
        self.assertEqual(len(contract.clusters), 1)


class GraphExecutorTests(unittest.TestCase):
    def _pattern(self, *, budget: int = 100) -> GraphPattern:
        return GraphPattern(
            pattern_id="gp1",
            nodes=(
                NodeConstraint(
                    "v_a1", "a1", ("tracklet",), "person", camera_ids=("cam-1",)
                ),
                NodeConstraint(
                    "v_a2", "a2", ("detection",), "bag", camera_ids=("cam-1",)
                ),
            ),
            edges=(EdgeConstraint("v_a1", "carries", "v_a2"),),
            join_budget=budget,
        )

    def test_bounded_join_and_trace(self) -> None:
        nodes = (
            EvidenceNode("t1", "tracklet", "v", "cam-1", 0, 4, {"label": "person"}, "p"),
            EvidenceNode("d1", "detection", "v", "cam-1", 1, 2, {"label": "bag"}, "p"),
        )
        edges = (EvidenceEdge("e1", "t1", "carries", "d1", 1, 2, {}, "p"),)
        result = execute_pattern(self._pattern(), nodes, edges)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.trace.edge_ids, ("e1",))
        self.assertFalse(result.trace.join_budget_reached)

    def test_join_budget_is_visible(self) -> None:
        nodes = tuple(
            EvidenceNode(f"t{i}", "tracklet", "v", "cam-1", i, i + 1, {"label": "person"}, "p")
            for i in range(5)
        ) + tuple(
            EvidenceNode(f"d{i}", "detection", "v", "cam-1", i, i + 1, {"label": "bag"}, "p")
            for i in range(5)
        )
        result = execute_pattern(self._pattern(budget=2), nodes, ())
        self.assertTrue(result.trace.join_budget_reached)
        self.assertIn("budget", result.trace.unresolved_reason or "")

    def test_sql_is_parameterized_against_malicious_video_id(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE evidence_nodes (
              node_id TEXT PRIMARY KEY, node_type TEXT, video_id TEXT,
              t0 REAL, t1 REAL, payload_json TEXT, producer_version TEXT
            );
            CREATE TABLE evidence_edges (
              edge_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
              object_node_id TEXT, t0 REAL, t1 REAL, evidence_json TEXT,
              producer_version TEXT
            );
            INSERT INTO evidence_nodes VALUES
              ('t1','tracklet','safe',0,1,'{"label":"person","camera_id":"cam-1"}','p');
            """
        )
        malicious = "safe' OR 1=1 --"
        pattern = GraphPattern(
            pattern_id="gp",
            nodes=(
                NodeConstraint(
                    "v_a1",
                    "a1",
                    ("tracklet",),
                    "person",
                    video_ids=(malicious,),
                ),
            ),
        )
        result = execute_sqlite(connection, pattern)
        self.assertEqual(result.candidates, ())
        self.assertEqual(
            connection.execute("SELECT count(*) FROM evidence_nodes").fetchone()[0], 1
        )
        connection.close()

    def test_sqlite_executor_reads_only_active_graph_generation(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE videos (video_id TEXT PRIMARY KEY, camera_id TEXT);
            CREATE TABLE evidence_nodes (
              node_id TEXT PRIMARY KEY, node_type TEXT, video_id TEXT,
              t0 REAL, t1 REAL, payload_json TEXT, producer_version TEXT
            );
            CREATE TABLE evidence_edges (
              edge_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
              object_node_id TEXT, t0 REAL, t1 REAL, evidence_json TEXT,
              producer_version TEXT
            );
            CREATE TABLE graph_generation_nodes (
              generation_key TEXT, node_id TEXT
            );
            CREATE TABLE graph_generation_edges (
              generation_key TEXT, edge_id TEXT
            );
            CREATE TABLE active_graph_generations (
              video_id TEXT PRIMARY KEY, generation_key TEXT
            );
            CREATE VIEW active_evidence_nodes AS
              SELECT n.* FROM evidence_nodes n
              JOIN graph_generation_nodes m ON m.node_id=n.node_id
              JOIN active_graph_generations a
                ON a.generation_key=m.generation_key;
            CREATE VIEW active_evidence_edges AS
              SELECT e.* FROM evidence_edges e
              JOIN graph_generation_edges m ON m.edge_id=e.edge_id
              JOIN active_graph_generations a
                ON a.generation_key=m.generation_key;
            INSERT INTO videos VALUES ('v','cam-1');
            INSERT INTO evidence_nodes VALUES
              ('stale','detection','v',0,1,'{"label":"person"}','old'),
              ('active','detection','v',10,11,'{"label":"person"}','new');
            INSERT INTO graph_generation_nodes VALUES
              ('g-old','stale'),('g-new','active');
            INSERT INTO active_graph_generations VALUES ('v','g-new');
            """
        )
        pattern = GraphPattern(
            pattern_id="active-only",
            nodes=(
                NodeConstraint(
                    "v_a1",
                    "a1",
                    ("detection",),
                    "person",
                    camera_ids=("cam-1",),
                    video_ids=("v",),
                ),
            ),
        )
        result = execute_sqlite(connection, pattern)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].evidence[0].node_id, "active")
        self.assertNotIn("stale", result.trace.node_ids)
        connection.close()

    def test_partial_active_view_migration_refuses_legacy_fallback(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE evidence_nodes (
              node_id TEXT, node_type TEXT, video_id TEXT, t0 REAL, t1 REAL,
              payload_json TEXT, producer_version TEXT
            );
            CREATE TABLE evidence_edges (
              edge_id TEXT, subject_node_id TEXT, predicate TEXT,
              object_node_id TEXT, t0 REAL, t1 REAL, evidence_json TEXT,
              producer_version TEXT
            );
            CREATE VIEW active_evidence_nodes AS SELECT * FROM evidence_nodes;
            """
        )
        pattern = GraphPattern(
            pattern_id="partial",
            nodes=(NodeConstraint("v_a1", "a1", ("detection",), "person"),),
        )
        with self.assertRaisesRegex(RuntimeError, "incomplete active evidence"):
            execute_sqlite(connection, pattern)
        connection.close()

    def test_visible_none_and_count_safety_in_executor(self) -> None:
        observation = EvidenceNode(
            "w1",
            "window",
            "v",
            "cam-1",
            0,
            10,
            {
                "label": "scene",
                "expected_ticks": 10,
                "observed_ticks": 10,
                "assessable_ticks": 10,
                "coverage_complete": True,
            },
            "p",
        )
        none_pattern = GraphPattern(
            pattern_id="none",
            nodes=(NodeConstraint("v_a1", "a1", ("tracklet",), "person"),),
            logic_constraints=(
                LogicConstraint(
                    "g1",
                    LogicOperator.VISIBLE_NONE,
                    ("v_a1",),
                    "candidate_episode",
                ),
            ),
        )
        result = execute_pattern(none_pattern, (observation,), ())
        self.assertEqual(result.logic_decisions["g1"].state, PredicateState.SUPPORTED)
        self.assertEqual(len(result.candidates), 1)

        conservative_window = EvidenceNode(
            "w-generic",
            "window",
            "v",
            "cam-1",
            0,
            10,
            {
                "label": "scene",
                "expected_ticks": 10,
                "observed_ticks": 10,
                "assessable_ticks": 10,
                "coverage_complete": True,
                "assessable": False,
                "region_assessable": False,
                "occlusion_assessed": False,
            },
            "p",
        )
        explicit_episode = EvidenceNode(
            "ep-assessed",
            "episode",
            "v",
            "cam-1",
            0,
            10,
            {
                "label": "assessed scene",
                "predicate_scope": "g1",
                "expected_ticks": 10,
                "observed_ticks": 10,
                "assessable_ticks": 10,
                "coverage_complete": True,
                "assessable": True,
                "region_assessable": True,
                "occlusion_assessed": True,
            },
            "query-safety-v1",
        )
        preferred = execute_pattern(
            none_pattern, (conservative_window, explicit_episode), ()
        )
        self.assertEqual(
            preferred.logic_decisions["g1"].state, PredicateState.SUPPORTED
        )

        unmatched_scope_pattern = GraphPattern(
            pattern_id="none-other",
            nodes=(NodeConstraint("v_a1", "a1", ("tracklet",), "person"),),
            logic_constraints=(
                LogicConstraint(
                    "g-other",
                    LogicOperator.VISIBLE_NONE,
                    ("v_a1",),
                    "candidate_episode",
                ),
            ),
        )
        conservative = execute_pattern(
            unmatched_scope_pattern,
            (conservative_window, explicit_episode),
            (),
        )
        self.assertEqual(
            conservative.logic_decisions["g-other"].state,
            PredicateState.UNOBSERVABLE,
        )

        incomplete = EvidenceNode(
            "w2",
            "window",
            "v",
            "cam-1",
            0,
            10,
            {
                "label": "scene",
                "expected_ticks": 10,
                "observed_ticks": 9,
                "assessable_ticks": 9,
                "coverage_complete": False,
            },
            "p",
        )
        unresolved = execute_pattern(none_pattern, (incomplete,), ())
        self.assertEqual(
            unresolved.logic_decisions["g1"].state, PredicateState.UNDETERMINED
        )

        count_pattern = GraphPattern(
            pattern_id="count",
            nodes=(NodeConstraint("v_a1", "a1", ("tracklet",), "person"),),
            logic_constraints=(
                LogicConstraint(
                    "g_count",
                    LogicOperator.COUNT,
                    ("v_a1",),
                    "candidate_episode",
                    min_count=1,
                    max_count=1,
                ),
            ),
        )
        fragmented = EvidenceNode(
            "t-fragment",
            "tracklet",
            "v",
            "cam-1",
            1,
            5,
            {"label": "person", "continuity": "interrupted"},
            "p",
        )
        count_result = execute_pattern(
            count_pattern, (observation, fragmented), ()
        )
        self.assertEqual(
            count_result.logic_decisions["g_count"].state,
            PredicateState.UNDETERMINED,
        )
        linked_track = EvidenceNode(
            "t-linked",
            "tracklet",
            "v",
            "cam-1",
            1,
            5,
            {"continuity": "continuous"},
            "p",
        )
        linked_detection = EvidenceNode(
            "d-person",
            "detection",
            "v",
            None,
            1,
            1,
            {"label": "person"},
            "p",
        )
        linked_edge = EvidenceEdge(
            "e-linked",
            "d-person",
            "belongs_to_track",
            "t-linked",
            1,
            1,
            {},
            "p",
        )
        linked_count = execute_pattern(
            count_pattern,
            (observation, linked_track, linked_detection),
            (linked_edge,),
        )
        self.assertEqual(
            linked_count.logic_decisions["g_count"].state,
            PredicateState.SUPPORTED,
        )

    def test_bounded_any_requires_only_one_alternative(self) -> None:
        pattern = GraphPattern(
            pattern_id="any",
            nodes=(
                NodeConstraint("v_a1", "a1", ("detection",), "red car"),
                NodeConstraint("v_a2", "a2", ("detection",), "blue van"),
            ),
            logic_constraints=(
                LogicConstraint(
                    "g1",
                    LogicOperator.ANY,
                    ("v_a1", "v_a2"),
                    "candidate_episode",
                ),
            ),
        )
        blue_van = EvidenceNode(
            "d-blue",
            "detection",
            "v",
            "cam-1",
            1,
            1,
            {"label": "blue van"},
            "p",
        )
        result = execute_pattern(pattern, (blue_van,), ())
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].atom_ids, ("a2",))
        self.assertEqual(
            result.logic_decisions["g1"].state, PredicateState.SUPPORTED
        )


class AssemblyTests(unittest.TestCase):
    def test_cross_window_order_same_camera_and_trace(self) -> None:
        plan = {
            "query_text": "enter then leave",
            "atoms": [
                {"atom_id": "a1", "text_span": "enters", "type": "action"},
                {"atom_id": "a2", "text_span": "exits", "type": "action"},
            ],
            "temporal_relations": [
                {
                    "first_atom": "a1",
                    "relation": "before",
                    "second_atom": "a2",
                    "max_gap_s": 600,
                }
            ],
        }
        result = assemble_temporal(
            plan,
            {
                "a1": [candidate("enter", 10, 14, atom_ids=("a1",))],
                "a2": [
                    candidate("exit", 200, 204, atom_ids=("a2",)),
                    candidate("wrong-order", 1, 4, atom_ids=("a2",)),
                    candidate("other-camera", 200, 204, camera="cam-2", atom_ids=("a2",)),
                ],
            },
        )
        self.assertEqual(len(result.episodes), 1)
        self.assertEqual(result.episodes[0].episode.t0, 10)
        self.assertEqual(result.episodes[0].episode.t1, 204)
        self.assertTrue(result.episodes[0].episode.trace_ids)
        reasons = {item.reason for item in result.rejected_joins}
        self.assertIn("wrong_order_or_overlapping_anchors", reasons)
        self.assertIn("cross_camera_or_video_join", reasons)

    def test_episode_cap_marks_incomplete_without_dropping_anchors(self) -> None:
        plan = {
            "query_text": "enter before exit",
            "atoms": [
                {"atom_id": "a1", "text_span": "enter", "type": "action"},
                {"atom_id": "a2", "text_span": "exit", "type": "action"},
            ],
            "temporal_relations": [
                {
                    "first_atom": "a1",
                    "relation": "before",
                    "second_atom": "a2",
                    "max_gap_s": 100,
                }
            ],
        }
        result = assemble_temporal(
            plan,
            {
                "a1": [candidate("a", 0, 1), candidate("b", 2, 3)],
                "a2": [candidate("c", 10, 11), candidate("d", 12, 13)],
            },
            max_episode_count=1,
        )
        self.assertEqual(result.completeness.anchor_candidates_qualifying, 4)
        self.assertEqual(result.completeness.anchor_candidates_retained, 4)
        self.assertTrue(result.completeness.episode_cap_bound)
        self.assertFalse(result.completeness.assembly_complete)


if __name__ == "__main__":
    unittest.main()
