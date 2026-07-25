"""Config catalogue, ablation catalogue, and the no-fabrication guarantee.

The synthetic PredictionsBundle here is an IN-MEMORY test fixture only, exercising
the scoring path. It is never written to a shipped panel and never presented as a
real measurement.
"""

import unittest

from eval import baselines as B
from eval.schema import NOT_YET_MEASURED, is_measured


class TestConfigCatalogue(unittest.TestCase):
    def test_six_baselines(self):
        self.assertEqual(set(B.BASELINES), {"B1", "B2", "B3", "FULL", "FULL+GRAPH", "FULL+STR"})
        self.assertEqual(len(B.BASELINE_ORDER), 6)

    def test_full_str_requires_trained_model(self):
        self.assertTrue(B.BASELINES["FULL+STR"].requires_trained_model)
        self.assertFalse(B.BASELINES["FULL"].requires_trained_model)

    def test_lane_composition(self):
        self.assertIn("temporal_reranker", B.BASELINES["FULL+STR"].lanes)
        self.assertIn("bounded_logic", B.BASELINES["FULL+GRAPH"].lanes)
        self.assertNotIn("constraint_verifier", B.BASELINES["B1"].lanes)
        self.assertIn("whole_query_verifier", B.BASELINES["B3"].lanes)

    def test_single_declared_budget_shared(self):
        budgets = {id(c.budget) for c in B.BASELINES.values()}
        self.assertEqual(len(budgets), 1)  # all configs share ONE declared budget

    def test_thirteen_ablations(self):
        self.assertEqual(len(B.ABLATIONS), 13)
        self.assertEqual([a.ablation_id for a in B.ABLATIONS], list(range(1, 14)))


class TestNoFabrication(unittest.TestCase):
    def test_none_bundle_is_not_yet_measured(self):
        families = [_fam("f1")]
        section = B.compute_config_metrics(families, None, B.BASELINES["FULL"])
        self.assertEqual(section["status"], NOT_YET_MEASURED)
        self.assertEqual(section["candidate_recall"], NOT_YET_MEASURED)
        self.assertEqual(section["temporal_set"], NOT_YET_MEASURED)


class TestScoringPath(unittest.TestCase):
    def test_measured_from_synthetic_predictions(self):
        families = [_fam("f1"), _fam_empty("f2")]
        bundle = B.PredictionsBundle(
            config="FULL", config_hash="deadbeef", produced_by="synthetic-test",
            predictions={
                "f1": {
                    "candidates": [_iv(10, 20)],
                    "ordered_clusters": [_iv(10, 20)],
                    "matches": [_iv(10, 20)],
                    "returned_empty": False,
                    "atom_predictions": {"a1": {"state": "supported"}},
                    "clusters": 3, "vlm_calls": 2,
                    "retrieval_latency_s": 1.0, "verification_latency_s": 4.0,
                    "end_to_end_latency_s": 5.0,
                    "coverage": {"embedded_ticks": 95, "expected_ticks": 100,
                                 "anchor_qualifying": 4, "anchor_retained": 4,
                                 "episode_cap_bound": False,
                                 "clusters_verified": 3, "clusters_total": 3},
                },
                "f2": {"returned_empty": True},
            },
        )
        section = B.compute_config_metrics(families, bundle, B.BASELINES["FULL"], iou_threshold=0.5)
        self.assertEqual(section["status"], "measured")
        self.assertEqual(section["candidate_recall"]["interval"], 1.0)
        self.assertEqual(section["temporal_set"]["f1"], 1.0)
        self.assertTrue(is_measured(section["empty_set_rejection_f1"]))
        self.assertEqual(section["required_condition"]["macro_f1"], 1.0)
        self.assertEqual(section["coverage"]["sampled_tick_coverage"], 0.95)
        self.assertTrue(section["coverage"]["assembly_complete"])


# --------------------------------------------------------------------------- #

def _iv(t0, t1):
    return {"video_id": "v", "camera_id": "c", "t0": t0, "t1": t1}


def _fam(fid):
    return {
        "family_id": fid, "capability_tags": ["object"],
        "ground_truth": {
            "intervals": {"cardinality": "one", "intervals": [_iv(10, 20)]},
            "atoms_relations": {"atoms": [
                {"atom_id": "a1", "text_span": "bag", "type": "object",
                 "required": True, "gt_state": "supported"}]},
        },
    }


def _fam_empty(fid):
    return {
        "family_id": fid, "capability_tags": ["empty_set"],
        "ground_truth": {
            "intervals": {"cardinality": "zero", "intervals": []},
            "atoms_relations": {"atoms": [
                {"atom_id": "a1", "text_span": "umbrella", "type": "object", "required": True}]},
        },
    }


if __name__ == "__main__":
    unittest.main()
