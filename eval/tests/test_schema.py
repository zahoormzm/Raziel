"""Validation-discipline tests: immutability, split leakage, malformed/unsupported
label rejection, visible_none / count safety, adjudication ordering, and the
not-yet-measured vs numeric distinction (stdlib unittest)."""

import copy
import unittest

from eval import schema as S


# --------------------------------------------------------------------------- #
# Minimal valid fixtures (synthetic)
# --------------------------------------------------------------------------- #

def valid_manifest():
    m = {
        "manifest_schema_version": "1.1.0",
        "session_id": "sess_x", "scenario_id": "scn_x", "pool": "staged",
        "camera_ids": ["cam_x"],
        "footage_files": [{
            "file_id": "sess_x_v1", "camera_id": "cam_x",
            "source_sha256": "0" * 64, "duration_s": 100.0, "synthetic": True,
        }],
        "authorization": {"status": "authorized", "consent_recorded": True},
        "provenance": {"collected_by": "m4", "collection_date": "2026-07-20"},
        "synthetic": True, "created_at": "2026-07-20T00:00:00",
    }
    return S.seal_manifest(m)


def valid_family(fid="fam_x", split="dev", cardinality="one"):
    ivs = [] if cardinality == "zero" else [{
        "video_id": "sess_x_v1", "camera_id": "cam_x", "t0": 10.0, "t1": 20.0,
    }]
    fam = {
        "family_schema_version": "1.1.0", "family_id": fid, "pool": "staged",
        "split": split, "scenario_id": "scn_x", "session_ids": ["sess_x"],
        "capability_tags": ["object"],
        "canonical_query": "a black bag near the gate",
        "paraphrases": [
            {"text": "a dark bag by the gate", "author_id": "a1", "written_independently": True},
            {"text": "a black holdall at the gate", "author_id": "a2", "written_independently": True},
        ],
        "scope": {"video_ids": ["sess_x_v1"], "camera_ids": ["cam_x"],
                  "start_time": None, "end_time": None,
                  "sampling_policy_version": "sampling-1.0.0"},
        "ground_truth": {
            "intervals": {"cardinality": cardinality, "intervals": ivs},
            "atoms_relations": {"atoms": [
                {"atom_id": "a1", "text_span": "black bag", "type": "object",
                 "required": True, "gt_state": "supported"},
            ]},
        },
        "labels": {"assessability": {"overall": "assessable", "reasons": ["clear"]}},
        "ground_truth_source": "human_ledger",
        "empty_set_review": {"required": cardinality == "zero", "review_complete": cardinality == "zero"},
        "synthetic": True,
    }
    if cardinality != "zero":
        fam["labels"]["boundary"] = {"start_pts": 10.0, "end_pts": 20.0,
                                     "tolerance_s": 1.0, "source": "human_ledger"}
    return fam


# --------------------------------------------------------------------------- #

class TestRegistry(unittest.TestCase):
    def test_all_schemas_load(self):
        reg = S.registry()
        for name in ("footage_session_manifest", "ledger_entry", "query_family",
                     "interval", "atom_relation_state", "challenger",
                     "assessability_boundary", "track_logic_ground_truth",
                     "annotation_record", "golden_case"):
            self.assertIn(f"{name}.schema.json", reg._docs)


class TestImmutability(unittest.TestCase):
    def test_seal_and_verify(self):
        m = valid_manifest()
        self.assertTrue(S.verify_manifest_hash(m))
        self.assertTrue(S.validate_footage_manifest(m).ok)

    def test_tamper_detected(self):
        m = valid_manifest()
        m["session_id"] = "tampered"
        self.assertFalse(S.verify_manifest_hash(m))
        self.assertFalse(S.validate_footage_manifest(m).ok)

    def test_missing_required_field_rejected(self):
        m = valid_manifest()
        del m["authorization"]
        self.assertFalse(S.validate_footage_manifest(m).ok)


class TestFamilyValidation(unittest.TestCase):
    def test_valid_family(self):
        self.assertTrue(S.validate_query_family(valid_family()).ok)

    def test_undetermined_gt_state_rejected(self):
        fam = valid_family()
        fam["ground_truth"]["atoms_relations"]["atoms"][0]["gt_state"] = "undetermined"
        r = S.validate_query_family(fam)
        self.assertFalse(r.ok)  # undetermined is never a ground-truth label

    def test_unsupported_logic_operator_rejected(self):
        fam = valid_family()
        fam["ground_truth"]["atoms_relations"]["logic_groups"] = [{
            "group_id": "g1", "operator": "xor", "atom_ids": ["a1"],
            "observation_scope": "candidate_episode", "gt_outcome": "satisfied",
        }]
        self.assertFalse(S.validate_query_family(fam).ok)

    def test_retriever_ground_truth_source_rejected(self):
        fam = valid_family()
        fam["ground_truth_source"] = "retriever"
        self.assertFalse(S.validate_query_family(fam).ok)

    def test_paraphrase_same_author_rejected(self):
        fam = valid_family()
        fam["paraphrases"][1]["author_id"] = fam["paraphrases"][0]["author_id"]
        self.assertFalse(S.validate_query_family(fam).ok)

    def test_paraphrase_count_must_be_two(self):
        fam = valid_family()
        fam["paraphrases"] = fam["paraphrases"][:1]
        self.assertFalse(S.validate_query_family(fam).ok)

    def test_cardinality_mismatch_rejected(self):
        fam = valid_family(cardinality="many")  # 'many' but only one interval
        self.assertFalse(S.validate_query_family(fam).ok)

    def test_empty_set_test_needs_review_complete(self):
        fam = valid_family(fid="fam_e", split="test", cardinality="zero")
        fam["empty_set_review"]["review_complete"] = False
        self.assertFalse(S.validate_query_family(fam).ok)

    def test_interval_t1_before_t0_rejected(self):
        fam = valid_family()
        fam["ground_truth"]["intervals"]["intervals"][0]["t1"] = 5.0  # < t0=10
        self.assertFalse(S.validate_query_family(fam).ok)


class TestTrackLogicSafety(unittest.TestCase):
    def test_visible_none_supported_requires_assessable_and_complete(self):
        tl = {"visible_none_gt": [{
            "group_id": "g1", "target": "bag",
            "observation_interval": {"video_id": "v", "camera_id": "c", "t0": 0, "t1": 10},
            "assessable": False, "expected_observation_ticks": 10,
            "observed_ticks_complete": False,
            "expected_outcome": "visible_absence_supported",
        }]}
        self.assertFalse(S.validate_track_logic(tl).ok)

    def test_visible_none_unobservable_ok(self):
        tl = {"visible_none_gt": [{
            "group_id": "g1", "target": "bag",
            "observation_interval": {"video_id": "v", "camera_id": "c", "t0": 0, "t1": 10},
            "assessable": False, "expected_observation_ticks": 10,
            "observed_ticks_complete": False, "expected_outcome": "unobservable",
        }]}
        self.assertTrue(S.validate_track_logic(tl).ok)

    def test_count_integer_under_high_fragmentation_rejected(self):
        tl = {"count_gt": [{
            "group_id": "g1",
            "continuous_camera_interval": {"video_id": "v", "camera_id": "c", "t0": 0, "t1": 10},
            "qualifying_tracklets": 3, "declared_bound": 5,
            "fragmentation_level": "high", "occlusion_level": "none", "expected_outcome": 3,
        }]}
        self.assertFalse(S.validate_track_logic(tl).ok)

    def test_count_exceeds_declared_bound_rejected(self):
        tl = {"count_gt": [{
            "group_id": "g1",
            "continuous_camera_interval": {"video_id": "v", "camera_id": "c", "t0": 0, "t1": 10},
            "qualifying_tracklets": 9, "declared_bound": 5,
            "fragmentation_level": "none", "occlusion_level": "none", "expected_outcome": 9,
        }]}
        self.assertFalse(S.validate_track_logic(tl).ok)


class TestSplitDiscipline(unittest.TestCase):
    def test_scenario_spanning_splits_rejected(self):
        f1 = valid_family(fid="f1", split="train")
        f2 = valid_family(fid="f2", split="test")  # same scenario scn_x, different split
        self.assertFalse(S.check_split_discipline([f1, f2]).ok)

    def test_session_leakage_rejected(self):
        f1 = valid_family(fid="f1", split="train")
        f2 = valid_family(fid="f2", split="train")
        f2["scenario_id"] = "scn_y"
        f2["split"] = "test"
        # same session sess_x under two splits
        self.assertFalse(S.check_split_discipline([f1, f2]).ok)

    def test_pool_bleed_rejected(self):
        f1 = valid_family(fid="f1", split="train")
        f2 = valid_family(fid="f2", split="train")
        f2["pool"] = "organizer"  # same scenario, two pools
        self.assertFalse(S.check_split_discipline([f1, f2]).ok)

    def test_clean_splits_ok(self):
        f1 = valid_family(fid="f1", split="train")
        f2 = copy.deepcopy(f1)
        f2["family_id"] = "f2"  # same scenario/session/split -> fine
        self.assertTrue(S.check_split_discipline([f1, f2]).ok)


class TestLedgerEntry(unittest.TestCase):
    def base(self):
        return {
            "ledger_schema_version": "1.0.0", "entry_id": "e1", "session_id": "sess_x",
            "camera_id": "cam_x", "video_id": "sess_x_v1", "start_pts": 10.0, "end_pts": 20.0,
            "actors": [{"anon_id": "P1"}], "objects": [{"obj_id": "o1", "label": "bag"}],
            "actions": [{"action": "places"}], "relations": [],
            "lighting": "good", "occlusion": "none",
            "assessability": {"overall": "assessable", "reasons": ["clear"]},
            "provenance": {"annotator_id": "A1", "annotated_at": "2026-07-20", "source": "human_watch"},
            "synthetic": True,
        }

    def test_valid(self):
        self.assertTrue(S.validate_ledger_entry(self.base()).ok)

    def test_end_before_start_rejected(self):
        e = self.base(); e["end_pts"] = 5.0
        self.assertFalse(S.validate_ledger_entry(e).ok)

    def test_biometric_actor_id_rejected(self):
        e = self.base(); e["actors"][0]["anon_id"] = "john_smith"
        self.assertFalse(S.validate_ledger_entry(e).ok)  # must match ^P[0-9]+$

    def test_retriever_source_rejected(self):
        e = self.base(); e["provenance"]["source"] = "retriever"
        self.assertFalse(S.validate_ledger_entry(e).ok)


class TestAnnotationOrdering(unittest.TestCase):
    def _pass(self, aid, ptype, ts, blind=True, adjudicates=None, annotator="A1"):
        rec = {"annotation_id": aid, "target_type": "query_family", "target_id": "fam_x",
               "annotator_id": annotator, "pass_type": ptype, "blind": blind,
               "recorded_at": ts, "labels": {"present_absent": "present"}, "synthetic": True}
        if adjudicates is not None:
            rec["adjudicates"] = adjudicates
        return rec

    def test_adjudication_after_independent_ok(self):
        recs = [
            self._pass("i1", "independent", "2026-07-20T10:00:00", annotator="A1"),
            self._pass("i2", "independent", "2026-07-20T10:05:00", annotator="A2"),
            self._pass("adj", "adjudication", "2026-07-21T09:00:00", blind=False,
                       adjudicates=["i1", "i2"]),
        ]
        self.assertTrue(S.check_annotation_ordering(recs).ok)

    def test_adjudication_before_independent_rejected(self):
        recs = [
            self._pass("i1", "independent", "2026-07-20T10:00:00", annotator="A1"),
            self._pass("i2", "independent", "2026-07-20T10:05:00", annotator="A2"),
            self._pass("adj", "adjudication", "2026-07-19T09:00:00", blind=False,
                       adjudicates=["i1", "i2"]),  # before both
        ]
        self.assertFalse(S.check_annotation_ordering(recs).ok)

    def test_adjudication_needs_two_refs(self):
        recs = [
            self._pass("i1", "independent", "2026-07-20T10:00:00"),
            self._pass("adj", "adjudication", "2026-07-21T09:00:00", blind=False,
                       adjudicates=["i1"]),
        ]
        self.assertFalse(S.check_annotation_ordering(recs).ok)

    def test_double_independent_must_be_blind(self):
        recs = [
            self._pass("i1", "independent", "2026-07-20T10:00:00", blind=False, annotator="A1"),
            self._pass("i2", "independent", "2026-07-20T10:05:00", blind=True, annotator="A2"),
        ]
        self.assertFalse(S.check_annotation_ordering(recs).ok)


class TestNotYetMeasured(unittest.TestCase):
    def test_sentinel_is_not_numeric(self):
        self.assertFalse(S.is_measured(S.NOT_YET_MEASURED))
        self.assertFalse(S.is_measured("0.9"))
        self.assertFalse(S.is_measured(True))  # bool is not a measurement

    def test_numeric_is_measured(self):
        self.assertTrue(S.is_measured(0.0))
        self.assertTrue(S.is_measured(42))
        self.assertTrue(S.is_measured(0.937))


if __name__ == "__main__":
    unittest.main()
