"""Tests for the Gate G3 parser audit harness (plan 13.4).

These test the *harness*, not the parser. The parser is Member 2's lane; this
suite makes sure the audit cannot silently report a pass it did not earn.
"""

import unittest

from eval import g3_audit as G


class TestAuditSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit_set = G.load_audit_set()
        cls.queries = cls.audit_set["queries"]

    def test_meets_plan_minimum_query_count(self):
        self.assertGreaterEqual(
            len(self.queries), 25, "plan 13.4 requires at least 25 scripted queries"
        )

    def test_every_required_category_present(self):
        present = {q["category"] for q in self.queries}
        missing = G.REQUIRED_CATEGORIES - present
        self.assertFalse(missing, f"audit set is missing categories: {sorted(missing)}")

    def test_query_ids_unique(self):
        ids = [q["id"] for q in self.queries]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_query_declares_expectations_and_severity(self):
        for query in self.queries:
            self.assertTrue(query.get("expect"), f"{query['id']}: no expectations")
            self.assertIn(
                query.get("failure_severity"), {"unsafe", "safe_abstain"},
                f"{query['id']}: failure_severity must be declared",
            )

    def test_threshold_matches_plan(self):
        self.assertAlmostEqual(self.audit_set["gate"]["pass_threshold"], 0.80)
        self.assertEqual(self.audit_set["gate"]["max_unsafe_failures"], 0)


class TestScoring(unittest.TestCase):
    """The scorer must actually detect the failures it claims to detect."""

    def _plan(self, **overrides):
        plan = {
            "interpretation_state": "clear",
            "atoms": [], "relations": [], "temporal_relations": [],
            "logic_groups": [], "ambiguities": [], "unsupported_constructs": [],
            "clarification_question": None,
        }
        plan.update(overrides)
        return plan

    def test_matching_plan_has_no_failures(self):
        case = {"text": "a black backpack", "expect": {"interpretation_state": "clear"}}
        self.assertEqual(G.check_query(case, self._plan()), [])

    def test_wrong_interpretation_state_is_caught(self):
        case = {"text": "x", "expect": {"interpretation_state": "clarification_required"}}
        self.assertTrue(G.check_query(case, self._plan()))

    def test_missing_logic_operator_is_caught(self):
        case = {"text": "no bag visible", "expect": {"logic_operator": "visible_none"}}
        failures = G.check_query(case, self._plan())
        self.assertTrue(any("visible_none" in f for f in failures))

    def test_wrong_count_bounds_are_caught(self):
        case = {"text": "exactly two people", "expect": {
            "logic_operator": "count", "logic_min_count": 2, "logic_max_count": 2}}
        plan = self._plan(logic_groups=[
            {"operator": "count", "atom_ids": ["a1"], "min_count": 3, "max_count": 3}])
        failures = G.check_query(case, plan)
        self.assertTrue(any("min_count" in f for f in failures))

    def test_invented_attribute_is_caught(self):
        """Plan 13.2: the parser cannot invent attributes."""
        case = {"text": "a backpack near the gate", "expect": {}}
        plan = self._plan(atoms=[
            {"atom_id": "a1", "text_span": "crimson", "type": "attribute"}])
        failures = G.check_query(case, plan)
        self.assertTrue(any("invented attribute" in f for f in failures))

    def test_canonicalised_action_is_not_an_invention(self):
        """'carrying' -> 'carries' is normalisation, not invention."""
        case = {"text": "a person carrying a bag", "expect": {}}
        plan = self._plan(atoms=[
            {"atom_id": "a1", "text_span": "carries", "type": "action"}])
        self.assertEqual(G.check_query(case, plan), [])

    def test_missing_temporal_relation_is_caught(self):
        case = {"text": "a before b", "expect": {
            "temporal_include": [["places", "before", "picks up"]]}}
        failures = G.check_query(case, self._plan())
        self.assertTrue(any("missing temporal" in f for f in failures))

    def test_forbidden_span_is_caught(self):
        case = {"text": "a black bag", "expect": {"forbid_atom_spans": ["blue"]}}
        plan = self._plan(atoms=[
            {"atom_id": "a1", "text_span": "blue bag", "type": "attribute"}])
        failures = G.check_query(case, plan)
        self.assertTrue(any("forbidden" in f for f in failures))


class TestGateLogic(unittest.TestCase):
    def test_audit_runs_end_to_end(self):
        report = G.run_audit()
        self.assertEqual(report["total_queries"], len(G.load_audit_set()["queries"]))
        self.assertTrue(report["meets_query_minimum"])
        self.assertEqual(report["missing_required_categories"], [])
        for result in report["results"]:
            self.assertIsNone(result["parser_error"], f"{result['id']} crashed the parser")

    def test_gate_is_currently_passing(self):
        """RELEASE_STATUS.md records G3 as Passed; assert it unconditionally.

        The neighbouring unsafe-failure test is conditional, so it passes
        vacuously whenever the gate is healthy *and* whenever the scorer stops
        detecting unsafe parses. Without this, a parser regression could drop the
        score below threshold while CI stayed green and the release document kept
        claiming a pass.
        """
        report = G.run_audit()
        self.assertGreaterEqual(report["score"], report["pass_threshold"])
        self.assertEqual(report["unsafe_failures"], 0)
        self.assertTrue(report["gate_passed"])

    def test_unsafe_failure_blocks_the_gate_regardless_of_score(self):
        """A score above threshold must not carry the gate while a confident
        wrong parse stands. Plan 13.4 requires failures to be survivable, not
        merely infrequent."""
        report = G.run_audit()
        if report["unsafe_failures"] > report["max_unsafe_failures"]:
            self.assertFalse(
                report["gate_passed"],
                "gate reported passed while unsafe failures remain",
            )

    def test_abstaining_downgrades_severity(self):
        """If the parser asked for clarification, the failure is survivable
        whatever the query's declared severity."""
        report = G.run_audit()
        for result in report["results"]:
            if result["fully_correct"]:
                continue
            if result["parser_abstained"]:
                self.assertEqual(result["severity"], "safe_abstain", result["id"])

    def test_report_formats_without_error(self):
        self.assertIn("Gate G3", G.format_report(G.run_audit()))


if __name__ == "__main__":
    unittest.main()
