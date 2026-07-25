from __future__ import annotations

import unittest

from packages.contracts.candidates import CandidateSet
from packages.contracts.query_plan import GraphPattern as ContractGraphPattern
from packages.contracts.query_plan import QueryPlan as ContractQueryPlan
from query.assemble import assemble_temporal, assembly_completeness_payload
from query.compiler import compile_query, graph_pattern_payload
from query.fuse import (
    candidate_set_payload,
    cluster_candidates,
    reciprocal_rank_order,
    union_semantic_and_graph,
)
from query.graph_execute import EvidenceEdge, EvidenceNode, execute_pattern, graph_trace_payload
from query.parser import deterministic_parse
from query.retrieve import ChannelScoreInput, ScoreTick, WindowSpan, retrieve_channels
from query.schema import query_plan_payload


class QueryIntelligencePipelineTests(unittest.TestCase):
    def test_semantic_graph_union_and_cross_window_assembly_contract(self) -> None:
        plan = deterministic_parse("person enters then exits")
        ContractQueryPlan.model_validate(query_plan_payload(plan))
        compiled = compile_query(plan)
        ContractGraphPattern.model_validate(graph_pattern_payload(compiled))

        action_atoms = [atom for atom in plan.atoms if atom.type.value == "action"]
        self.assertGreaterEqual(len(action_atoms), 2)
        first_atom, second_atom = action_atoms[:2]

        windows = (
            WindowSpan("early", "video-1", "cam-1", 0, 12),
            WindowSpan("late", "video-1", "cam-1", 120, 132),
        )
        early_ticks = (
            ScoreTick("e0", 1, "video-1", "cam-1", 1, 0.91),
            ScoreTick("e1", 2, "video-1", "cam-1", 5, 0.88),
        )
        late_ticks = (
            ScoreTick("l0", 3, "video-1", "cam-1", 121, 0.93),
            ScoreTick("l1", 4, "video-1", "cam-1", 125, 0.89),
        )
        semantic = retrieve_channels(
            (
                ChannelScoreInput(
                    f"frame:atom:{first_atom.atom_id}",
                    early_ticks,
                    threshold=0.8,
                    aggregation="max",
                    atom_ids=(first_atom.atom_id,),
                ),
                ChannelScoreInput(
                    f"frame:atom:{second_atom.atom_id}",
                    late_ticks,
                    threshold=0.8,
                    aggregation="max",
                    atom_ids=(second_atom.atom_id,),
                ),
            ),
            windows,
        )

        graph_nodes = (
            EvidenceNode(
                "track:enter",
                "tracklet",
                "video-1",
                "cam-1",
                2,
                8,
                {"label": "person enters", "continuity": "continuous"},
                "graph-v1",
            ),
            EvidenceNode(
                "track:exit",
                "tracklet",
                "video-1",
                "cam-1",
                122,
                128,
                {"label": "person exits", "continuity": "continuous"},
                "graph-v1",
            ),
        )
        graph = execute_pattern(compiled.graph_pattern, graph_nodes, ())
        fused = union_semantic_and_graph(semantic.candidates, graph.candidates)
        semantic_ids = {item.candidate_id for item in semantic.candidates}
        self.assertTrue(semantic_ids.issubset({item.candidate_id for item in fused}))

        ordered = reciprocal_rank_order(fused)
        anchors = {
            atom.atom_id: [
                item for item in ordered if atom.atom_id in item.atom_ids
            ]
            for atom in (first_atom, second_atom)
        }
        assembled = assemble_temporal(plan, anchors)
        self.assertTrue(assembled.episodes)
        clusters = cluster_candidates(
            [episode.episode for episode in assembled.episodes]
        )
        payload = candidate_set_payload(
            search_id="integration-search",
            query_plan_version=plan.schema_version,
            channels_run=semantic.channels_run + ("graph:pattern",),
            exact_scoring_completed=semantic.exact_scoring_completed,
            windows=ordered,
            clusters=clusters,
            graph_execution=graph_trace_payload(graph.trace),
            assembly=assembly_completeness_payload(assembled.completeness),
        )
        contract = CandidateSet.model_validate(payload)
        self.assertTrue(contract.exact_scoring_completed)
        self.assertTrue(contract.assembly.assembly_complete)
        self.assertGreaterEqual(len(contract.windows), len(semantic.candidates))


if __name__ == "__main__":
    unittest.main()
