"""Golden end-to-end suite structural test (§29), in its conventional location.

Runnable standalone::

    python -m unittest discover -s tests/golden -t tests/golden -v

It inserts the repo root on sys.path so the Member-4 ``eval`` package validators
are reused (single source of truth). The ten cases are the §29 required suite and
MUST be run cache-bypassed before every release candidate.
"""

import json
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from eval import schema as S  # noqa: E402

SUITE_DIR = Path(__file__).resolve().parent / "suite"

REQUIRED_TITLES = {
    1: "object", 2: "attribute", 3: "action", 4: "binding", 5: "cross-window",
    6: "absent", 7: "unobservable", 8: "disjunction", 9: "visible", 10: "count",
}


class TestGoldenSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [json.loads(p.read_text(encoding="utf-8"))
                     for p in sorted(SUITE_DIR.glob("*.json"))]

    def test_ten_cases_present(self):
        self.assertEqual(len(self.cases), 10, "the §29 golden suite requires ten queries")

    def test_indices_unique_and_complete(self):
        self.assertEqual(sorted(c["required_index"] for c in self.cases), list(range(1, 11)))

    def test_each_case_valid_and_synthetic(self):
        for c in self.cases:
            with self.subTest(case=c["case_id"]):
                self.assertTrue(c["synthetic"])
                r = S.validate_golden_case(c)
                self.assertTrue(r.ok, f"{c['case_id']}: {r.errors}")

    def test_titles_match_required_topics(self):
        for c in self.cases:
            token = REQUIRED_TITLES[c["required_index"]]
            haystack = (c["title"] + " " + c["case_id"]).lower()
            self.assertIn(token, haystack, f"case {c['required_index']} should mention {token!r}")


if __name__ == "__main__":
    unittest.main()
