"""Benchmark-panel tests: config hash, brand, not-yet-measured rendering, and the
§22.4 text block."""

import unittest

from eval import panel as P
from eval.schema import NOT_YET_MEASURED


def _inputs(extra=None):
    base = {"plan_version": "v1.3", "iou_threshold": 0.5, "family_count": 42}
    if extra:
        base.update(extra)
    return base


class TestConfigHash(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(P.eval_config_hash(_inputs()), P.eval_config_hash(_inputs()))

    def test_changes_with_inputs(self):
        self.assertNotEqual(P.eval_config_hash(_inputs()),
                            P.eval_config_hash(_inputs({"iou_threshold": 0.7})))


class TestBuildPanel(unittest.TestCase):
    def test_structure_and_brand(self):
        sections = {"FULL": {"config": "FULL", "status": NOT_YET_MEASURED,
                             "candidate_recall": NOT_YET_MEASURED,
                             "required_condition": NOT_YET_MEASURED,
                             "empty_set_rejection_f1": NOT_YET_MEASURED}}
        panel = P.build_panel({"family_count": 42}, sections,
                              eval_config_inputs=_inputs(), evaluated_date="2026-07-24",
                              iou_threshold=0.5)
        self.assertEqual(panel["product"]["product_name"], "RAZIEL")
        self.assertEqual(panel["product"]["retrieval_name"], "Eyes of God")
        self.assertEqual(panel["product"]["product_subtitle"], "Temporal Evidence Intelligence")
        self.assertIn("aggregate held-out measurements", panel["disclaimer"])
        self.assertFalse(P.any_measured(panel))

    def test_render_text_not_measured(self):
        sections = {"FULL": {"config": "FULL", "status": NOT_YET_MEASURED,
                             "candidate_recall": NOT_YET_MEASURED,
                             "required_condition": NOT_YET_MEASURED,
                             "empty_set_rejection_f1": NOT_YET_MEASURED}}
        panel = P.build_panel({}, sections, eval_config_inputs=_inputs(),
                              evaluated_date="2026-07-24", iou_threshold=0.5)
        text = P.render_text(panel)
        self.assertIn("Interval candidate recall: not yet measured", text)
        self.assertIn("evaluated 2026-07-24", text)

    def test_render_text_measured(self):
        sections = {"FULL": {
            "config": "FULL", "status": "measured",
            "candidate_recall": {"interval_num": 40, "interval_den": 42,
                                 "complete_set_num": 18, "complete_set_den": 20},
            "empty_set_rejection_f1": 0.91,
            "required_condition": {"undetermined_rate": 0.05},
        }}
        panel = P.build_panel({}, sections, eval_config_inputs=_inputs(),
                              evaluated_date="2026-07-24", iou_threshold=0.5)
        text = P.render_text(panel)
        self.assertIn("Interval candidate recall: 40/42", text)
        self.assertIn("Complete-set positive-query recall: 18/20", text)
        self.assertIn("Empty-set rejection F1: 0.910", text)
        self.assertIn("System undetermined rate: 0.050", text)
        self.assertTrue(P.any_measured(panel))


if __name__ == "__main__":
    unittest.main()
