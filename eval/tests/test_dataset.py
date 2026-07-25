"""End-to-end validation of the on-disk synthetic seed dataset and golden suite.

Confirms the shipped artifacts satisfy Gate G9's structural requirements (§21.8):
>=40 families, challenger coverage, independent-agreement subset, and a clean
end-to-end evaluation run. (Numeric gates stay not_yet_measured until real
footage/results exist — see the dataset card.)
"""

import json
import unittest
from pathlib import Path

from eval import run_eval as R
from eval import schema as S

GOLDEN_DIR = S.REPO_ROOT / "tests" / "golden" / "suite"

ALL_CAPABILITIES = {
    "object", "attribute", "action", "binding", "location", "temporal_order",
    "multi_occurrence", "empty_set", "ambiguous", "bounded_or", "visible_absence",
    "bounded_count", "unobservable", "long_gap_identity_rejection",
}
KEY_TEST_CAPABILITIES = {
    "object", "attribute", "binding", "temporal_order", "empty_set",
    "unobservable", "visible_absence", "bounded_count",
}


class TestSeedDatasetOnDisk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.families = R.load_families()
        cls.manifests = R.load_manifests()
        cls.annotations = R.load_annotations()

    def test_minimum_family_count(self):
        self.assertGreaterEqual(len(self.families), 40, "plan requires >=40 families")

    def test_every_family_validates(self):
        for fam in self.families:
            with self.subTest(family=fam["family_id"]):
                r = S.validate_query_family(fam)
                self.assertTrue(r.ok, f"{fam['family_id']}: {r.errors}")

    def test_all_synthetic(self):
        self.assertTrue(all(f.get("synthetic") for f in self.families))

    def test_split_discipline(self):
        r = S.check_split_discipline(self.families)
        self.assertTrue(r.ok, r.errors)

    def test_manifests_immutable_and_valid(self):
        self.assertTrue(len(self.manifests) > 0)
        for m in self.manifests:
            with self.subTest(session=m["session_id"]):
                self.assertTrue(S.verify_manifest_hash(m))
                self.assertTrue(S.validate_footage_manifest(m).ok)

    def test_staged_and_organizer_pools_separate(self):
        pools = {f["scenario_id"]: f["pool"] for f in self.families}
        # each scenario has exactly one pool (guaranteed by split discipline too)
        self.assertTrue(all(p in ("staged", "organizer") for p in pools.values()))
        self.assertIn("organizer", pools.values())
        self.assertIn("staged", pools.values())

    def test_annotations_validate_and_order(self):
        for a in self.annotations:
            self.assertTrue(S.validate_annotation_record(a).ok, a["annotation_id"])
        self.assertTrue(S.check_annotation_ordering(self.annotations).ok)

    def test_double_annotation_at_least_20pct(self):
        frac = S.double_annotation_fraction([f["family_id"] for f in self.families],
                                            self.annotations)
        self.assertGreaterEqual(frac, 0.20)

    def test_capability_coverage_complete(self):
        seen = set()
        for f in self.families:
            seen.update(f.get("capability_tags", []))
        missing = ALL_CAPABILITIES - seen
        self.assertEqual(missing, set(), f"missing capabilities: {missing}")

    def test_test_split_covers_key_capabilities(self):
        test_caps = set()
        for f in self.families:
            if f["split"] == "test":
                test_caps.update(f.get("capability_tags", []))
        missing = KEY_TEST_CAPABILITIES - test_caps
        self.assertEqual(missing, set(), f"test split missing: {missing}")

    def test_empty_set_families_reviewed(self):
        for f in self.families:
            card = f["ground_truth"]["intervals"]["cardinality"]
            if card == "zero":
                self.assertTrue(f["empty_set_review"]["required"])
                if f["split"] == "test":
                    self.assertTrue(f["empty_set_review"]["review_complete"], f["family_id"])

    def test_challenger_coverage(self):
        types = set()
        for f in self.families:
            for ch in f.get("challengers", []):
                types.add(ch["type"])
        # a representative spread of typed challengers must be present
        for required in ("wrong_attribute", "wrong_binding", "wrong_order",
                         "track_fragmentation", "bounded_disjunction",
                         "visible_absence_assessable", "visible_absence_unassessable",
                         "bounded_count_correct", "bounded_count_incorrect",
                         "true_no_event", "unobservable", "visually_similar_actor"):
            self.assertIn(required, types, f"missing challenger type: {required}")

    def test_end_to_end_eval_runs(self):
        panel = R.run()  # no predictions -> not_yet_measured everywhere, but must run clean
        self.assertTrue(panel["data_integrity"]["ok"])
        self.assertEqual(panel["headline"]["interval_candidate_recall"], S.NOT_YET_MEASURED)


class TestGoldenSuiteOnDisk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [json.loads(p.read_text(encoding="utf-8"))
                     for p in sorted(GOLDEN_DIR.glob("*.json"))]

    def test_exactly_ten_cases(self):
        self.assertEqual(len(self.cases), 10)

    def test_required_indices_1_to_10(self):
        idx = sorted(c["required_index"] for c in self.cases)
        self.assertEqual(idx, list(range(1, 11)))

    def test_each_case_validates(self):
        for c in self.cases:
            with self.subTest(case=c["case_id"]):
                r = S.validate_golden_case(c)
                self.assertTrue(r.ok, f"{c['case_id']}: {r.errors}")

    def test_headline_matches_conclusion(self):
        mapping = {
            "verified_matches_found": "VERIFIED MATCH",
            "no_verified_match_at_operating_point": "NO VERIFIED MATCH",
            "insufficient_visual_evidence": "INSUFFICIENT VISUAL EVIDENCE",
        }
        for c in self.cases:
            concl = c["expected"]["archive_conclusion"]
            if concl in mapping:
                self.assertIn(mapping[concl], c["expected"]["headline_contains"], c["case_id"])

    def test_case9_has_assessable_and_occluded_variants(self):
        case9 = next(c for c in self.cases if c["required_index"] == 9)
        names = {v["name"] for v in case9["expected"]["variants"]}
        self.assertEqual(names, {"assessable", "occluded"})
        outcomes = {v["name"]: v["expected_outcome"] for v in case9["expected"]["variants"]}
        self.assertEqual(outcomes["assessable"], "visible_absence_supported")
        self.assertEqual(outcomes["occluded"], "unobservable")

    def test_case10_has_fragmentation_decoy_unresolved(self):
        case10 = next(c for c in self.cases if c["required_index"] == 10)
        decoy = next(v for v in case10["expected"]["variants"] if "decoy" in v["name"])
        self.assertEqual(decoy["expected_outcome"], "unresolved")
        # and its track_logic ground truth encodes the same
        count_gt = case10["family"]["ground_truth"]["track_logic"]["count_gt"]
        decoy_gt = next(g for g in count_gt if g["group_id"] == "g_decoy")
        self.assertEqual(decoy_gt["expected_outcome"], "unresolved")
        self.assertEqual(decoy_gt["fragmentation_level"], "high")

    def test_all_golden_synthetic(self):
        self.assertTrue(all(c["synthetic"] for c in self.cases))


if __name__ == "__main__":
    unittest.main()
