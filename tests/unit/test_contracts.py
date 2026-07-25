from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from packages.contracts.query_plan import QueryPlan
from packages.contracts.search_result import SearchResult
from packages.contracts.video_manifest import VideoManifest


FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures"


class ContractFixtureTests(unittest.TestCase):
    def load(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_video_manifest_keeps_failed_tick_in_denominator(self) -> None:
        manifest = VideoManifest.model_validate(self.load("video_manifest_success.json"))
        self.assertEqual(manifest.expected_ticks, 2)
        self.assertEqual(manifest.decode_failed_ticks, 1)
        self.assertEqual(manifest.scored_coverage, 0.5)

    def test_query_plan_fixture_validates(self) -> None:
        plan = QueryPlan.model_validate(self.load("query_plan_success.json"))
        self.assertEqual(len(plan.relations), 2)

    def test_all_public_result_states_validate(self) -> None:
        expected = {
            "search_result_success.json": "1 VERIFIED MATCH FOUND",
            "search_result_no_match.json": "NO VERIFIED MATCH AT CURRENT OPERATING POINT",
            "search_result_unobservable.json": "INSUFFICIENT VISUAL EVIDENCE",
            "search_result_undetermined.json": "NO RESULT — SYSTEM COULD NOT ASSESS 1 CANDIDATES",
            "search_result_budget_reached.json": "SEARCH INCOMPLETE",
        }
        for name, headline in expected.items():
            with self.subTest(name=name):
                result = SearchResult.model_validate(self.load(name))
                self.assertEqual(result.headline(), headline)

    def test_budget_truncation_cannot_be_clean_no_match(self) -> None:
        payload = self.load("search_result_budget_reached.json")
        payload["archive_conclusion"] = "no_verified_match_at_operating_point"
        with self.assertRaises(ValidationError):
            SearchResult.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
