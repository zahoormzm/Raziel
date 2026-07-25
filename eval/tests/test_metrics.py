"""Numeric-correctness tests for every §22.1 metric (stdlib unittest)."""

import unittest

from eval import metrics as m


def iv(t0, t1, video="v", camera="c"):
    return {"video_id": video, "camera_id": camera, "t0": t0, "t1": t1}


class TestIntervalGeometry(unittest.TestCase):
    def test_iou_identical(self):
        self.assertEqual(m.t_iou(iv(0, 10), iv(0, 10)), 1.0)

    def test_iou_half_overlap(self):
        self.assertAlmostEqual(m.t_iou(iv(0, 10), iv(5, 15)), 1 / 3)

    def test_iou_disjoint(self):
        self.assertEqual(m.t_iou(iv(0, 10), iv(20, 30)), 0.0)

    def test_iou_cross_camera_is_zero(self):
        self.assertEqual(m.t_iou(iv(0, 10, camera="c1"), iv(0, 10, camera="c2")), 0.0)

    def test_iou_cross_video_is_zero(self):
        self.assertEqual(m.t_iou(iv(0, 10, video="v1"), iv(0, 10, video="v2")), 0.0)


class TestMatching(unittest.TestCase):
    def test_one_to_one_enforced(self):
        # Two predictions overlap the same single GT; only one may match.
        preds = [iv(0, 10), iv(1, 9)]
        gts = [iv(0, 10)]
        r = m.one_to_one_match(preds, gts, threshold=0.5)
        self.assertEqual(len(r.matches), 1)
        self.assertEqual(len(r.unmatched_pred), 1)
        self.assertEqual(len(r.unmatched_gt), 0)

    def test_deterministic_best_iou_wins(self):
        preds = [iv(1, 9), iv(0, 10)]  # second is the exact match
        gts = [iv(0, 10)]
        r = m.one_to_one_match(preds, gts, 0.5)
        self.assertEqual(r.matches[0].pred_index, 1)


class TestCandidateRecall(unittest.TestCase):
    def test_interval_recall_overlap(self):
        cands = [iv(0, 5), iv(100, 110)]
        gts = [iv(1, 4), iv(200, 210)]
        self.assertEqual(m.interval_candidate_recall(cands, gts, 0.0), 0.5)

    def test_complete_set_requires_all(self):
        queries = [
            {"candidates": [iv(0, 5), iv(10, 15)], "gts": [iv(1, 4), iv(11, 14)]},  # full
            {"candidates": [iv(0, 5)], "gts": [iv(1, 4), iv(200, 210)]},           # partial
        ]
        self.assertEqual(m.complete_set_query_recall(queries, 0.0), 0.5)

    def test_recall_within_budget_truncates(self):
        ordered = [iv(0, 5), iv(100, 110)]
        gts = [iv(1, 4), iv(101, 109)]
        self.assertEqual(m.recall_within_budget(ordered, gts, budget_k=1, iou_threshold=0.0), 0.5)

    def test_empty_gts_returns_none(self):
        self.assertIsNone(m.interval_candidate_recall([iv(0, 5)], [], 0.0))


class TestTemporalSetPRF(unittest.TestCase):
    def test_perfect(self):
        preds = [iv(0, 10), iv(100, 110)]
        gts = [iv(1, 9), iv(101, 109)]
        prf = m.temporal_set_prf(preds, gts, 0.5)
        self.assertEqual((prf.tp, prf.fp, prf.fn), (2, 0, 0))
        self.assertEqual(prf.f1, 1.0)

    def test_extra_pred_is_fp(self):
        preds = [iv(0, 10), iv(500, 600)]
        gts = [iv(0, 10)]
        prf = m.temporal_set_prf(preds, gts, 0.5)
        self.assertEqual((prf.tp, prf.fp, prf.fn), (1, 1, 0))

    def test_micro_aggregation(self):
        queries = [
            {"preds": [iv(0, 10)], "gts": [iv(0, 10)]},
            {"preds": [], "gts": [iv(0, 10)]},
        ]
        prf = m.temporal_set_prf_micro(queries, 0.5)
        self.assertEqual((prf.tp, prf.fp, prf.fn), (1, 0, 1))


class TestEmptySetRejection(unittest.TestCase):
    def test_counts(self):
        fams = [
            {"is_empty": True, "returned_empty": True},    # TP
            {"is_empty": True, "returned_empty": False},   # FN (hallucinated match)
            {"is_empty": False, "returned_empty": True},   # FP (wrong rejection)
            {"is_empty": False, "returned_empty": False},  # true negative (ignored)
        ]
        r = m.empty_set_rejection_f1(fams)
        self.assertEqual((r.prf.tp, r.prf.fp, r.prf.fn), (1, 1, 1))
        self.assertAlmostEqual(r.prf.f1, 0.5)


class TestMacroF1(unittest.TestCase):
    def test_perfect_three_classes(self):
        items = [{"gt": s, "pred": s} for s in ("supported", "contradicted", "unobservable")]
        r = m.required_condition_macro_f1(items)
        self.assertEqual(r.macro_f1, 1.0)
        self.assertEqual(r.undetermined_rate, 0.0)

    def test_undetermined_counts_as_incorrect(self):
        items = [
            {"gt": "supported", "pred": "supported"},
            {"gt": "contradicted", "pred": "contradicted"},
            {"gt": "unobservable", "pred": "undetermined", "reason": "timeout"},
        ]
        r = m.required_condition_macro_f1(items)
        self.assertAlmostEqual(r.macro_f1, 2 / 3)  # unobservable class F1 = 0
        self.assertAlmostEqual(r.undetermined_rate, 1 / 3)
        self.assertEqual(r.undetermined_by_reason, {"timeout": 1})
        self.assertEqual(r.per_class["unobservable"].f1, 0.0)

    def test_rejects_undetermined_as_ground_truth(self):
        with self.assertRaises(ValueError):
            m.required_condition_macro_f1([{"gt": "undetermined", "pred": "supported"}])

    def test_rejects_unknown_pred(self):
        with self.assertRaises(ValueError):
            m.required_condition_macro_f1([{"gt": "supported", "pred": "banana"}])


class TestBoundaryError(unittest.TestCase):
    def test_medians(self):
        pairs = [(iv(0, 10), iv(1, 12)), (iv(100, 110), iv(103, 111))]
        be = m.boundary_error(pairs)
        # start errors [1,3] -> median 2; end errors [2,1] -> median 1.5
        self.assertEqual(be.median_start_error_s, 2.0)
        self.assertEqual(be.median_end_error_s, 1.5)

    def test_empty_none(self):
        self.assertIsNone(m.boundary_error([]))


class TestLatencyEfficiency(unittest.TestCase):
    def test_percentile_linear(self):
        self.assertAlmostEqual(m.percentile(list(range(1, 21)), 0.95), 19.05)
        self.assertEqual(m.percentile([5], 0.95), 5)

    def test_latency_stats(self):
        s = m.latency_stats([1, 2, 3, 4])
        self.assertEqual(s.median_s, 2.5)
        self.assertEqual(s.n, 4)

    def test_indexing_throughput(self):
        self.assertEqual(m.indexing_throughput(1000, 10), 100)
        self.assertIsNone(m.indexing_throughput(1000, 0))


class TestCoverage(unittest.TestCase):
    def test_sampled_tick_coverage(self):
        self.assertAlmostEqual(m.sampled_tick_coverage(90, 100), 0.9)
        self.assertIsNone(m.sampled_tick_coverage(0, 0))

    def test_assembly_complete_only_if_all_retained_and_no_cap(self):
        a = m.assembly_completeness(10, 10, False)
        self.assertTrue(a.complete)
        b = m.assembly_completeness(10, 9, False)
        self.assertFalse(b.complete)
        c = m.assembly_completeness(10, 10, True)  # cap bound
        self.assertFalse(c.complete)


class TestOptionalLanes(unittest.TestCase):
    def test_count_mae_excludes_unresolved(self):
        pairs = [(2, 2), (3, 4), ("unresolved", 5)]
        self.assertAlmostEqual(m.count_mae(pairs), 0.5)  # only (2,2),(3,4)

    def test_count_mae_all_unresolved_none(self):
        self.assertIsNone(m.count_mae([("unresolved", 2)]))

    def test_bounded_logic_false_clean_negative_string(self):
        items = [{"operator": "visible_none", "gt_outcome": "unobservable",
                  "pred_outcome": "visible_absence_supported", "under_stress": True}]
        r = m.bounded_logic_accuracy_by_operator(items)
        self.assertEqual(r.false_clean_negative_count, 1)

    def test_bounded_logic_false_clean_negative_integer_count(self):
        # An integer count asserted where the truth demands 'unresolved' is a false clean negative.
        items = [{"operator": "count", "gt_outcome": "unresolved",
                  "pred_outcome": 3, "under_stress": True}]
        r = m.bounded_logic_accuracy_by_operator(items)
        self.assertEqual(r.false_clean_negative_count, 1)

    def test_bounded_logic_correct_abstention_not_penalized(self):
        items = [{"operator": "count", "gt_outcome": "unresolved",
                  "pred_outcome": "unresolved", "under_stress": True}]
        r = m.bounded_logic_accuracy_by_operator(items)
        self.assertEqual(r.false_clean_negative_count, 0)
        self.assertEqual(r.accuracy_by_operator["count"], 1.0)


if __name__ == "__main__":
    unittest.main()
