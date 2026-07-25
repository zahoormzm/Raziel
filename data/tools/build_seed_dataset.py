"""Deterministic SYNTHETIC seed dataset generator (Member 4).

Produces a fully schema-valid, plan-aligned **synthetic** seed for the RAZIEL
data lane so the schemas, validators, metrics, and the ten-query golden suite can
be exercised end to end BEFORE real authorized footage exists. Nothing here is
real footage or a real result: every artifact carries ``synthetic: true`` and no
model numbers are produced. The seed is the scaffold that human annotators
replace, family by family, once footage is authorized (see data/ledger.md §8).

Run::

    python -m data.tools.build_seed_dataset          # writes + validates
    python -m data.tools.build_seed_dataset --check   # validate existing, do not write

Outputs (all under repo paths owned by Member 4):
    data/manifests/<session>.json          immutable, content-hashed
    data/queries/families/<family>.json    >=40 query families (target 60-80)
    data/annotations/<...>.json            blind double annotation + adjudication (>=20%)
    tests/golden/suite/<case>.json         the §29 ten-query golden suite

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §21, §29.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script or a module.
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from eval import schema as S  # noqa: E402

SAMPLING_POLICY_VERSION = "sampling-1.0.0"

MANIFESTS_DIR = _REPO / "data" / "manifests"
FAMILIES_DIR = _REPO / "data" / "queries" / "families"
ANNOTATIONS_DIR = _REPO / "data" / "annotations"
GOLDEN_DIR = _REPO / "tests" / "golden" / "suite"


# --------------------------------------------------------------------------- #
# Sessions (each session is its own scenario -> split leakage impossible by
# construction; different recording sessions of a similar setup may sit in
# different splits, which is exactly what §21.5 wants).
# --------------------------------------------------------------------------- #
# session_id: (pool, split, camera, duration_s, authorization_status, consent)
SESSIONS = {
    "sess_gate_a":   ("staged",    "train", "gate",   600.0, "authorized", True),
    "sess_gate_b":   ("staged",    "test",  "gate",   600.0, "authorized", True),
    "sess_entrance": ("staged",    "train", "ent",    600.0, "authorized", True),
    "sess_court":    ("staged",    "dev",   "court",  480.0, "authorized", True),
    "sess_court_b":  ("staged",    "test",  "court",  480.0, "authorized", True),
    "sess_corr":     ("staged",    "dev",   "corr",   420.0, "authorized", True),
    "sess_corr_b":   ("staged",    "test",  "corr",   420.0, "authorized", True),
    "sess_hat":      ("staged",    "train", "hat",    360.0, "authorized", True),
    "sess_dark":     ("staged",    "dev",   "dark",   360.0, "authorized", True),
    "sess_dark_b":   ("staged",    "test",  "dark",   360.0, "authorized", True),
    "sess_lobby":    ("staged",    "test",  "lobby",  600.0, "authorized", True),
    "sess_lobby_b":  ("staged",    "train", "lobby",  600.0, "authorized", True),
    "sess_cross":    ("staged",    "train", "cross",  900.0, "authorized", True),
    "sess_cross_b":  ("staged",    "test",  "cross",  900.0, "authorized", True),
    "sess_similar":  ("staged",    "dev",   "sim",    420.0, "authorized", True),
    "sess_amb":      ("staged",    "train", "amb",    300.0, "authorized", True),
    "sess_longgap":  ("staged",    "dev",   "long",  1200.0, "authorized", True),
    "sess_org_a":    ("organizer", "train", "orgA",   600.0, "pending",    False),
    "sess_org_b":    ("organizer", "test",  "orgB",   600.0, "pending",    False),
    "sess_golden":   ("staged",    "test",  "gold",   900.0, "authorized", True),
}


def _scenario(session: str) -> str:
    return "scn_" + session.replace("sess_", "")


def _video(session: str) -> str:
    return session + "_v1"


def _camera(session: str) -> str:
    return "cam_" + SESSIONS[session][2]


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

def boundary(start, end, tol=1.0, source="human_ledger"):
    return {"start_pts": start, "end_pts": end, "tolerance_s": tol, "source": source}


def interval(session, t0, t1, blabel=None, group=None):
    iv = {"video_id": _video(session), "camera_id": _camera(session), "t0": t0, "t1": t1}
    if blabel:
        iv["boundary"] = blabel
    if group:
        iv["repeated_event_group"] = group
    return iv


def intervals(cardinality, items):
    return {"cardinality": cardinality, "intervals": items}


def assess(overall, reasons, per_attr=None):
    a = {"overall": overall, "reasons": reasons}
    if per_attr:
        a["per_attribute"] = per_attr
    return a


def atom(aid, span, typ, required, state=None, role=None, reason=None, vis=None):
    a = {"atom_id": aid, "text_span": span, "type": typ, "required": required}
    if role:
        a["role"] = role
    if vis is not None:
        a["visibility_sensitive"] = vis
    if state:
        a["gt_state"] = state
    if reason:
        a["gt_reason"] = reason
    return a


def relation(rid, subj, pred, obj, required, state=None):
    r = {"relation_id": rid, "subject_atom": subj, "predicate": pred,
         "object_atom": obj, "required": required}
    if state:
        r["gt_state"] = state
    return r


def temporal_rel(first, rel, second, max_gap=None, same_actor=False):
    t = {"first_atom": first, "relation": rel, "second_atom": second,
         "same_actor_required": same_actor}
    if max_gap is not None:
        t["max_gap_s"] = max_gap
    return t


def logic_group(gid, operator, atom_ids, scope, outcome, min_c=None, max_c=None):
    g = {"group_id": gid, "operator": operator, "atom_ids": atom_ids,
         "observation_scope": scope, "gt_outcome": outcome}
    g["min_count"] = min_c
    g["max_count"] = max_c
    return g


def atoms_relations(atoms, relations=None, temporal_relations=None, logic_groups=None):
    ar = {"atoms": atoms}
    if relations:
        ar["relations"] = relations
    if temporal_relations:
        ar["temporal_relations"] = temporal_relations
    if logic_groups:
        ar["logic_groups"] = logic_groups
    return ar


def challenger(cid, fid, ctype, desc, conclusion, rejected_reason=None,
               atom_states=None, logic_outcome=None):
    exp = {"archive_conclusion": conclusion}
    if rejected_reason:
        exp["rejected_reason"] = rejected_reason
    if atom_states:
        exp["atom_states"] = atom_states
    if logic_outcome:
        exp["logic_outcome"] = logic_outcome
    return {"challenger_id": cid, "family_id": fid, "type": ctype,
            "description": desc, "expected": exp, "synthetic": True}


def paras(p1, a1, p2, a2):
    return [{"text": p1, "author_id": a1, "written_independently": True},
            {"text": p2, "author_id": a2, "written_independently": True}]


def scope(session, start=None, end=None):
    return {"video_ids": [_video(session)], "camera_ids": [_camera(session)],
            "start_time": start, "end_time": end,
            "sampling_policy_version": SAMPLING_POLICY_VERSION}


def obs_interval(session, t0, t1):
    return {"video_id": _video(session), "camera_id": _camera(session), "t0": t0, "t1": t1}


def family(fid, session, canonical, para, ar, ivs, labels, tags,
           challengers=None, empty_review=None, track_logic=None, notes=None):
    pool, split, *_ = SESSIONS[session]
    gt = {"intervals": ivs, "atoms_relations": ar}
    if track_logic:
        gt["track_logic"] = track_logic
    f = {
        "family_schema_version": "1.1.0",
        "family_id": fid,
        "pool": pool,
        "split": split,
        "scenario_id": _scenario(session),
        "session_ids": [session],
        "capability_tags": tags,
        "canonical_query": canonical,
        "paraphrases": para,
        "scope": scope(session),
        "ground_truth": gt,
        "labels": labels,
        "ground_truth_source": "human_ledger",
        "empty_set_review": empty_review or {"required": False, "review_complete": False},
        "synthetic": True,
    }
    if challengers:
        f["challengers"] = challengers
    if notes:
        f["notes"] = notes
    return f


REVIEWED = {"required": True, "reviewed_by": ["annotator_A1", "annotator_A2"],
            "review_complete": True}


# --------------------------------------------------------------------------- #
# The 42 families
# --------------------------------------------------------------------------- #

def build_families() -> list[dict]:
    F: list[dict] = []

    # === sess_gate_a (staged/train) ===
    F.append(family(
        "fam_0001", "sess_gate_a",
        "a black backpack near the gate",
        paras("show a dark backpack by the gate", "auth_1",
              "find a black rucksack next to the gate", "auth_2"),
        atoms_relations([
            atom("a1", "black backpack", "object", True, "supported", role="candidate_anchor", vis=True),
            atom("a2", "black", "attribute", True, "supported", reason="visible_match", vis=True),
            atom("a3", "the gate", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_gate_a", 42.0, 55.0, boundary(42.0, 55.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(42.0, 55.0)},
        ["object", "attribute", "location"],
        challengers=[
            challenger("ch_0001a", "fam_0001", "wrong_attribute",
                       "a blue backpack near the gate", "no_verified_match_at_operating_point",
                       rejected_reason="black bag contradicted because the bag is blue",
                       atom_states=[{"atom_id": "a2", "state": "contradicted"}]),
            challenger("ch_0001b", "fam_0001", "wrong_object",
                       "a black suitcase (not a backpack) near the gate",
                       "no_verified_match_at_operating_point",
                       atom_states=[{"atom_id": "a1", "state": "contradicted"}]),
        ],
    ))
    F.append(family(
        "fam_0002", "sess_gate_a",
        "a person in red places a black bag near the gate and walks away",
        paras("someone wearing red sets down a dark bag by the gate then leaves", "auth_1",
              "a red-clothed person drops a black bag at the gate and departs", "auth_3"),
        atoms_relations(
            [
                atom("a1", "person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "red", "attribute", True, "supported", vis=True),
                atom("a3", "black bag", "object", True, "supported", vis=True),
                atom("a4", "near the gate", "location", True, "supported", role="filter"),
                atom("a5", "places", "action", True, "supported"),
                atom("a6", "walks away", "action", True, "supported"),
            ],
            relations=[
                relation("r1", "a1", "places", "a3", True, "supported"),
                relation("r2", "a3", "near", "a4", True, "supported"),
            ],
            temporal_relations=[temporal_rel("a5", "before", "a6", max_gap=30, same_actor=True)],
        ),
        intervals("one", [interval("sess_gate_a", 120.0, 138.0, boundary(120.0, 138.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(120.0, 138.0)},
        ["object", "attribute", "binding", "action", "temporal_order", "location"],
        challengers=[
            challenger("ch_0002a", "fam_0002", "wrong_binding",
                       "a different person (not in red) places the bag while the red person only stands by",
                       "no_verified_match_at_operating_point",
                       rejected_reason="places(person_in_red, bag) contradicted"),
            challenger("ch_0002b", "fam_0002", "wrong_order",
                       "the person walks away first and only later places a bag",
                       "no_verified_match_at_operating_point",
                       rejected_reason="temporal order places-before-walk-away contradicted"),
            challenger("ch_0002c", "fam_0002", "partial_event",
                       "the person places the bag but never walks away (stays in frame)",
                       "no_verified_match_at_operating_point",
                       atom_states=[{"atom_id": "a6", "state": "contradicted"}]),
        ],
    ))
    F.append(family(
        "fam_0003", "sess_gate_a",
        "someone leaves a bag on the ground",
        paras("a person sets a bag down and leaves it", "auth_2",
              "somebody abandons a bag on the floor", "auth_3"),
        atoms_relations(
            [
                atom("a1", "someone", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "a bag", "object", True, "supported"),
                atom("a3", "leaves on the ground", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "places", "a2", True, "supported")],
        ),
        intervals("one", [interval("sess_gate_a", 210.0, 224.0, boundary(210.0, 224.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(210.0, 224.0)},
        ["object", "action"],
        challengers=[
            challenger("ch_0003a", "fam_0003", "short_interruption",
                       "the person sets the bag down but picks it up again two seconds later "
                       "(not abandonment)", "no_verified_match_at_operating_point",
                       rejected_reason="abandonment contradicted by immediate pick-up"),
        ],
    ))
    F.append(family(
        "fam_0004", "sess_gate_a",
        "a person walks past the gate",
        paras("someone passes by the gate on foot", "auth_1",
              "a pedestrian goes past the gate", "auth_2"),
        atoms_relations([
            atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "walks past", "action", True, "supported"),
            atom("a3", "the gate", "location", True, "supported", role="filter"),
        ]),
        intervals("many", [
            interval("sess_gate_a", 60.0, 66.0, boundary(60.0, 66.0), group="grp_walk"),
            interval("sess_gate_a", 300.0, 306.0, boundary(300.0, 306.0), group="grp_walk"),
            interval("sess_gate_a", 500.0, 507.0, boundary(500.0, 507.0), group="grp_walk"),
        ]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(60.0, 66.0)},
        ["action", "multi_occurrence", "location"],
        challengers=[
            challenger("ch_0004a", "fam_0004", "repeated_events",
                       "three separate pass-bys must all be returned (complete-set)",
                       "verified_matches_found"),
        ],
    ))
    F.append(family(
        "fam_0042", "sess_gate_a",
        "a person wearing a red hoodie near the gate",
        paras("someone in a red hooded top by the gate", "auth_3",
              "a person in a red hoodie standing at the gate", "auth_1"),
        atoms_relations([
            atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "red hoodie", "attribute", True, "supported", vis=True),
            atom("a3", "the gate", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_gate_a", 400.0, 412.0, boundary(400.0, 412.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(400.0, 412.0)},
        ["object", "attribute", "location"],
        challengers=[
            challenger("ch_0042a", "fam_0042", "wrong_attribute",
                       "a person in a grey hoodie near the gate",
                       "no_verified_match_at_operating_point",
                       atom_states=[{"atom_id": "a2", "state": "contradicted"}]),
        ],
    ))

    # === sess_gate_b (staged/test) ===
    F.append(family(
        "fam_0005", "sess_gate_b",
        "a black bag by the entrance gate",
        paras("a dark bag at the entrance gate", "auth_2",
              "a black holdall next to the gate entrance", "auth_3"),
        atoms_relations([
            atom("a1", "black bag", "object", True, "supported", role="candidate_anchor", vis=True),
            atom("a2", "black", "attribute", True, "supported", vis=True),
            atom("a3", "entrance gate", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_gate_b", 88.0, 101.0, boundary(88.0, 101.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(88.0, 101.0)},
        ["object", "attribute", "location"],
        challengers=[
            challenger("ch_0005a", "fam_0005", "wrong_attribute",
                       "a blue bag by the entrance gate", "no_verified_match_at_operating_point",
                       atom_states=[{"atom_id": "a2", "state": "contradicted"}]),
        ],
    ))
    F.append(family(
        "fam_0006", "sess_gate_b",
        "a person in a red top drops a dark bag at the gate",
        paras("someone wearing a red shirt sets a dark bag at the gate", "auth_1",
              "a red-topped person lets a dark bag down by the gate", "auth_2"),
        atoms_relations(
            [
                atom("a1", "person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "red top", "attribute", True, "supported", vis=True),
                atom("a3", "dark bag", "object", True, "supported", vis=True),
                atom("a4", "the gate", "location", True, "supported", role="filter"),
                atom("a5", "drops", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "places", "a3", True, "supported")],
        ),
        intervals("one", [interval("sess_gate_b", 205.0, 219.0, boundary(205.0, 219.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(205.0, 219.0)},
        ["object", "attribute", "binding", "action"],
        challengers=[
            challenger("ch_0006a", "fam_0006", "wrong_binding",
                       "a person NOT in red drops the bag", "no_verified_match_at_operating_point",
                       rejected_reason="binding contradicted"),
            challenger("ch_0006b", "fam_0006", "wrong_order",
                       "bag is already down before the red person arrives",
                       "no_verified_match_at_operating_point"),
        ],
    ))
    F.append(family(
        "fam_0007", "sess_gate_b",
        "a red jacket near the gate",
        paras("someone in a red jacket by the gate", "auth_3",
              "a person wearing a red coat close to the gate", "auth_1"),
        atoms_relations([
            atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "red jacket", "attribute", True, "supported", vis=True),
            atom("a3", "the gate", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_gate_b", 330.0, 345.0, boundary(330.0, 345.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(330.0, 345.0)},
        ["object", "attribute", "location"],
        challengers=[
            challenger("ch_0007a", "fam_0007", "wrong_attribute",
                       "a green jacket near the gate", "no_verified_match_at_operating_point",
                       atom_states=[{"atom_id": "a2", "state": "contradicted"}]),
        ],
    ))

    # === sess_entrance (staged/train) ===
    F.append(family(
        "fam_0008", "sess_entrance",
        "a red vehicle near the entrance",
        paras("a red car by the entrance", "auth_1", "a red van close to the entrance", "auth_2"),
        atoms_relations([
            atom("a1", "vehicle", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "red", "attribute", True, "supported", vis=True),
            atom("a3", "the entrance", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_entrance", 30.0, 48.0, boundary(30.0, 48.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(30.0, 48.0)},
        ["object", "attribute", "location"],
        challengers=[
            challenger("ch_0008a", "fam_0008", "wrong_attribute",
                       "a white van near the entrance", "no_verified_match_at_operating_point",
                       atom_states=[{"atom_id": "a2", "state": "contradicted"}]),
        ],
    ))
    F.append(family(
        "fam_0009", "sess_entrance",
        "a white van at the entrance",
        paras("a white delivery van at the entrance", "auth_3",
              "a pale-coloured van by the entrance", "auth_1"),
        atoms_relations([
            atom("a1", "van", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "white", "attribute", True, "supported", vis=True),
            atom("a3", "the entrance", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_entrance", 150.0, 172.0, boundary(150.0, 172.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(150.0, 172.0)},
        ["object", "attribute", "location"],
    ))
    F.append(family(
        "fam_0010", "sess_entrance",
        "any vehicle parked by the loading bay",
        paras("a parked vehicle at the loading bay", "auth_2",
              "a car or van stopped by the loading bay", "auth_3"),
        atoms_relations([
            atom("a1", "vehicle", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "parked", "action", True, "supported"),
            atom("a3", "loading bay", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_entrance", 260.0, 300.0, boundary(260.0, 300.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(260.0, 300.0)},
        ["object", "location"],
    ))
    F.append(family(
        "fam_0039", "sess_entrance",
        "a motorcycle by the entrance",
        paras("a motorbike near the entrance", "auth_1",
              "a two-wheeler parked at the entrance", "auth_2"),
        atoms_relations([
            atom("a1", "motorcycle", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "the entrance", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_entrance", 360.0, 375.0, boundary(360.0, 375.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(360.0, 375.0)},
        ["object", "location"],
    ))

    # === sess_court (staged/dev) — bounded count ===
    F.append(family(
        "fam_0011", "sess_court",
        "count the people standing in the courtyard",
        paras("how many people are standing in the courtyard", "auth_1",
              "number of people gathered in the courtyard", "auth_2"),
        atoms_relations(
            [atom("a1", "people standing", "object", True, "supported", role="candidate_anchor")],
            logic_groups=[logic_group("g1", "count", ["a1"], "continuous_camera_interval",
                                      "satisfied", min_c=1, max_c=5)],
        ),
        intervals("one", [interval("sess_court", 40.0, 120.0, boundary(40.0, 120.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(40.0, 120.0)},
        ["bounded_count"],
        track_logic={"count_gt": [{
            "group_id": "g1",
            "continuous_camera_interval": obs_interval("sess_court", 40.0, 120.0),
            "qualifying_tracklets": 2, "declared_bound": 5,
            "fragmentation_level": "none", "occlusion_level": "none",
            "expected_outcome": 2,
        }]},
        challengers=[
            challenger("ch_0011a", "fam_0011", "bounded_count_incorrect",
                       "claiming three people when only two qualify",
                       "no_verified_match_at_operating_point", logic_outcome="not_satisfied"),
            challenger("ch_0011b", "fam_0011", "track_fragmentation",
                       "one person's track fragments into two; must not inflate the count",
                       "no_verified_match_at_operating_point", logic_outcome="unresolved"),
        ],
    ))

    # === sess_court_b (staged/test) — bounded count ===
    F.append(family(
        "fam_0012", "sess_court_b",
        "how many people cross the courtyard",
        paras("count people crossing the courtyard", "auth_2",
              "number of people walking across the courtyard", "auth_3"),
        atoms_relations(
            [atom("a1", "people crossing", "object", True, "unobservable", role="candidate_anchor")],
            logic_groups=[logic_group("g1", "count", ["a1"], "continuous_camera_interval",
                                      "unresolved", min_c=1, max_c=5)],
        ),
        intervals("one", [interval("sess_court_b", 30.0, 200.0, boundary(30.0, 200.0))]),
        {"assessability": assess("partially_assessable", ["occlusion"]), "boundary": boundary(30.0, 200.0)},
        ["bounded_count"],
        track_logic={"count_gt": [{
            "group_id": "g1",
            "continuous_camera_interval": obs_interval("sess_court_b", 30.0, 200.0),
            "qualifying_tracklets": 3, "declared_bound": 5,
            "fragmentation_level": "high", "occlusion_level": "partial",
            "expected_outcome": "unresolved",
        }]},
        notes="Honest abstention: heavy fragmentation forces 'unresolved', never a fabricated count.",
        challengers=[
            challenger("ch_0012a", "fam_0012", "bounded_count_correct",
                       "a clean sub-interval where exactly two people cross",
                       "verified_matches_found", logic_outcome="satisfied"),
        ],
    ))
    F.append(family(
        "fam_0013", "sess_court_b",
        "two people waiting by the bench",
        paras("a pair of people standing at the bench", "auth_1",
              "two individuals waiting near the bench", "auth_2"),
        atoms_relations(
            [atom("a1", "people waiting", "object", True, "supported", role="candidate_anchor"),
             atom("a2", "the bench", "location", True, "supported", role="filter")],
            logic_groups=[logic_group("g1", "count", ["a1"], "continuous_camera_interval",
                                      "satisfied", min_c=2, max_c=2)],
        ),
        intervals("one", [interval("sess_court_b", 250.0, 320.0, boundary(250.0, 320.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(250.0, 320.0)},
        ["bounded_count", "location"],
        track_logic={"count_gt": [{
            "group_id": "g1",
            "continuous_camera_interval": obs_interval("sess_court_b", 250.0, 320.0),
            "qualifying_tracklets": 2, "declared_bound": 5,
            "fragmentation_level": "none", "occlusion_level": "none",
            "expected_outcome": 2,
        }]},
    ))

    # === sess_corr (staged/dev) — visible absence assessable ===
    F.append(family(
        "fam_0014", "sess_corr",
        "the corridor has no bag left in it for the whole clip",
        paras("there is no bag anywhere in the corridor during the clip", "auth_1",
              "no bag is present in the corridor at any point", "auth_2"),
        atoms_relations(
            [atom("a1", "a bag", "object", True, "contradicted", role="candidate_anchor", vis=True)],
            logic_groups=[logic_group("g1", "visible_none", ["a1"], "candidate_episode",
                                      "not_satisfied")],
        ),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["visible_absence", "empty_set"],
        empty_review=REVIEWED,
        track_logic={"visible_none_gt": [{
            "group_id": "g1", "target": "bag",
            "observation_interval": obs_interval("sess_corr", 0.0, 420.0),
            "assessable": True, "expected_observation_ticks": 420,
            "observed_ticks_complete": True,
            "expected_outcome": "visible_absence_supported",
        }]},
        notes="Assessable visible absence: absence can be certified because the region is "
              "assessable and observation ticks are complete.",
        challengers=[
            challenger("ch_0014a", "fam_0014", "visible_absence_unassessable",
                       "the same claim over an occluded stretch cannot certify absence",
                       "insufficient_visual_evidence", logic_outcome="unobservable"),
        ],
    ))

    # === sess_corr_b (staged/test) — visible absence occluded + assessable ===
    F.append(family(
        "fam_0015", "sess_corr_b",
        "no unattended bag in the corridor",
        paras("the corridor is free of any unattended bag", "auth_2",
              "there is no bag left unattended in the corridor", "auth_3"),
        atoms_relations(
            [atom("a1", "an unattended bag", "object", True, "unobservable", role="candidate_anchor", vis=True)],
            logic_groups=[logic_group("g1", "visible_none", ["a1"], "candidate_episode",
                                      "unobservable")],
        ),
        intervals("zero", []),
        {"assessability": assess("unassessable", ["occlusion", "missing_coverage"])},
        ["visible_absence", "unobservable", "empty_set"],
        empty_review=REVIEWED,
        track_logic={"visible_none_gt": [{
            "group_id": "g1", "target": "unattended bag",
            "observation_interval": obs_interval("sess_corr_b", 0.0, 420.0),
            "assessable": False, "expected_observation_ticks": 420,
            "observed_ticks_complete": False,
            "expected_outcome": "unobservable",
        }]},
        notes="Occluded/incomplete coverage: absence CANNOT be certified — outcome is "
              "unobservable, never a clean negative (§14.3).",
        challengers=[
            challenger("ch_0015a", "fam_0015", "visible_absence_assessable",
                       "a clean assessable counterpart where absence is certifiable",
                       "no_verified_match_at_operating_point", logic_outcome="satisfied"),
        ],
    ))
    F.append(family(
        "fam_0016", "sess_corr_b",
        "the loading bay has no vehicle during the clip",
        paras("no vehicle is present at the loading bay in the clip", "auth_1",
              "the loading bay stays empty of vehicles throughout", "auth_2"),
        atoms_relations(
            [atom("a1", "a vehicle", "object", True, "contradicted", role="candidate_anchor", vis=True)],
            logic_groups=[logic_group("g1", "visible_none", ["a1"], "candidate_episode",
                                      "not_satisfied")],
        ),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["visible_absence", "empty_set"],
        empty_review=REVIEWED,
        track_logic={"visible_none_gt": [{
            "group_id": "g1", "target": "vehicle",
            "observation_interval": obs_interval("sess_corr_b", 0.0, 420.0),
            "assessable": True, "expected_observation_ticks": 420,
            "observed_ticks_complete": True,
            "expected_outcome": "visible_absence_supported",
        }]},
    ))

    # === sess_hat (staged/train) — bounded disjunction ===
    F.append(family(
        "fam_0017", "sess_hat",
        "a person wearing a red or blue hat",
        paras("someone in a red or blue cap", "auth_1",
              "a person with a hat that is either red or blue", "auth_3"),
        atoms_relations(
            [
                atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "red hat", "attribute", False, "contradicted", vis=True),
                atom("a3", "blue hat", "attribute", False, "supported", vis=True),
            ],
            logic_groups=[logic_group("g1", "any", ["a2", "a3"], "candidate_episode", "satisfied")],
        ),
        intervals("one", [interval("sess_hat", 55.0, 68.0, boundary(55.0, 68.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(55.0, 68.0)},
        ["bounded_or", "attribute"],
        track_logic={"disjunction_gt": [{
            "group_id": "g1", "alternatives": ["red hat", "blue hat"],
            "present_alternatives": ["blue hat"], "expected_outcome": "satisfied",
        }]},
        challengers=[
            challenger("ch_0017a", "fam_0017", "bounded_disjunction",
                       "a person in a green hat (neither red nor blue)",
                       "no_verified_match_at_operating_point", logic_outcome="not_satisfied"),
        ],
    ))
    F.append(family(
        "fam_0018", "sess_hat",
        "someone carrying a red or green bag",
        paras("a person with a red or green bag", "auth_2",
              "somebody holding a bag that is red or green", "auth_1"),
        atoms_relations(
            [
                atom("a1", "someone", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "red bag", "attribute", False, "supported", vis=True),
                atom("a3", "green bag", "attribute", False, "contradicted", vis=True),
            ],
            relations=[relation("r1", "a1", "carries", "a2", True, "supported")],
            logic_groups=[logic_group("g1", "any", ["a2", "a3"], "candidate_episode", "satisfied")],
        ),
        intervals("one", [interval("sess_hat", 180.0, 194.0, boundary(180.0, 194.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(180.0, 194.0)},
        ["bounded_or", "attribute", "binding"],
        track_logic={"disjunction_gt": [{
            "group_id": "g1", "alternatives": ["red bag", "green bag"],
            "present_alternatives": ["red bag"], "expected_outcome": "satisfied",
        }]},
    ))
    F.append(family(
        "fam_0040", "sess_hat",
        "a person in a red or yellow jacket",
        paras("someone wearing a red or yellow coat", "auth_3",
              "a person whose jacket is red or yellow", "auth_2"),
        atoms_relations(
            [
                atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "red jacket", "attribute", False, "supported", vis=True),
                atom("a3", "yellow jacket", "attribute", False, "contradicted", vis=True),
            ],
            logic_groups=[logic_group("g1", "any", ["a2", "a3"], "candidate_episode", "satisfied")],
        ),
        intervals("one", [interval("sess_hat", 300.0, 312.0, boundary(300.0, 312.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(300.0, 312.0)},
        ["bounded_or", "attribute"],
        track_logic={"disjunction_gt": [{
            "group_id": "g1", "alternatives": ["red jacket", "yellow jacket"],
            "present_alternatives": ["red jacket"], "expected_outcome": "satisfied",
        }]},
    ))

    # === sess_dark (staged/dev) — unobservable ===
    F.append(family(
        "fam_0019", "sess_dark",
        "a person in a dark jacket in the alley",
        paras("someone wearing a dark coat in the alley", "auth_1",
              "a person in a black jacket down the alley", "auth_2"),
        atoms_relations([
            atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "dark jacket colour", "attribute", True, "unobservable",
                 reason="low_light", vis=True),
            atom("a3", "the alley", "location", True, "supported", role="filter"),
        ]),
        intervals("zero", []),
        {"assessability": assess("partially_assessable", ["low_light", "dark"])},
        ["unobservable"],
        empty_review=REVIEWED,
        notes="Honest uncertainty: a person is present but jacket colour is unassessable in the "
              "dark, so the required attribute is unobservable -> INSUFFICIENT VISUAL EVIDENCE, "
              "not a verified match and not a clean no-match.",
        challengers=[
            challenger("ch_0019a", "fam_0019", "unobservable",
                       "darkness prevents judging the colour constraint",
                       "insufficient_visual_evidence",
                       atom_states=[{"atom_id": "a2", "state": "unobservable"}]),
        ],
    ))

    # === sess_dark_b (staged/test) — unobservable ===
    F.append(family(
        "fam_0020", "sess_dark_b",
        "a green bag in the alley at night",
        paras("a green-coloured bag in the dark alley", "auth_2",
              "a bag that looks green down the night-time alley", "auth_3"),
        atoms_relations([
            atom("a1", "a bag", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "green colour", "attribute", True, "unobservable", reason="low_light", vis=True),
            atom("a3", "the alley", "location", True, "supported", role="filter"),
        ]),
        intervals("zero", []),
        {"assessability": assess("partially_assessable", ["low_light", "dark"])},
        ["unobservable"],
        empty_review=REVIEWED,
        notes="Colour unassessable at night -> INSUFFICIENT VISUAL EVIDENCE.",
    ))
    F.append(family(
        "fam_0021", "sess_dark_b",
        "the licence plate of the parked car",
        paras("read the number plate of the parked car", "auth_1",
              "the registration plate on the parked vehicle", "auth_2"),
        atoms_relations([
            atom("a1", "the parked car", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "licence plate text", "attribute", True, "unobservable",
                 reason="out_of_frame", vis=True),
        ]),
        intervals("zero", []),
        {"assessability": assess("unassessable", ["resolution", "out_of_frame"])},
        ["unobservable"],
        empty_review=REVIEWED,
        notes="Plate not legible at this resolution/angle -> unobservable, no fabricated read.",
    ))

    # === sess_lobby (staged/test) — true empty-set ===
    F.append(family(
        "fam_0022", "sess_lobby",
        "a person carrying a yellow umbrella in the lobby",
        paras("someone with a yellow umbrella in the lobby", "auth_2",
              "a person holding a yellow brolly in the lobby", "auth_3"),
        atoms_relations([
            atom("a1", "a person", "object", True, role="candidate_anchor"),
            atom("a2", "yellow umbrella", "object", True, vis=True),
        ]),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["empty_set"],
        empty_review=REVIEWED,
        notes="Reviewed full scope: no such event -> NO VERIFIED MATCH AT CURRENT OPERATING POINT.",
        challengers=[
            challenger("ch_0022a", "fam_0022", "true_no_event",
                       "no yellow umbrella appears anywhere in the reviewed scope",
                       "no_verified_match_at_operating_point"),
        ],
    ))
    F.append(family(
        "fam_0023", "sess_lobby",
        "a bicycle inside the lobby",
        paras("a bike in the lobby", "auth_1", "a pushbike inside the lobby", "auth_2"),
        atoms_relations([atom("a1", "a bicycle", "object", True, role="candidate_anchor")]),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["empty_set"],
        empty_review=REVIEWED,
    ))

    # === sess_lobby_b (staged/train) — empty-set ===
    F.append(family(
        "fam_0024", "sess_lobby_b",
        "a red suitcase left in the lobby",
        paras("an abandoned red suitcase in the lobby", "auth_3",
              "a red case left unattended in the lobby", "auth_1"),
        atoms_relations([
            atom("a1", "a red suitcase", "object", True, role="candidate_anchor", vis=True),
            atom("a2", "left unattended", "action", True),
        ]),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["empty_set"],
        empty_review=REVIEWED,
    ))

    # === sess_cross (staged/train) — cross-window temporal order ===
    F.append(family(
        "fam_0025", "sess_cross",
        "a person enters near the gate, then later leaves carrying a bag",
        paras("someone comes in by the gate and afterwards exits with a bag", "auth_1",
              "a person arrives at the gate and later departs holding a bag", "auth_2"),
        atoms_relations(
            [
                atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "enters", "action", True, "supported"),
                atom("a3", "near the gate", "location", True, "supported", role="filter"),
                atom("a4", "leaves", "action", True, "supported"),
                atom("a5", "a bag", "object", True, "supported"),
                atom("a6", "carrying", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "carries", "a5", True, "supported")],
            temporal_relations=[temporal_rel("a2", "before", "a4", max_gap=600, same_actor=True)],
        ),
        intervals("one", [interval("sess_cross", 60.0, 430.0, boundary(60.0, 430.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(60.0, 430.0)},
        ["temporal_order", "action", "binding", "location"],
        notes="Same-actor binding holds only because the actor stays visibly trackable within "
              "the episode; no long-gap identity is claimed.",
        challengers=[
            challenger("ch_0025a", "fam_0025", "wrong_order",
                       "the person leaves with a bag first and enters later",
                       "no_verified_match_at_operating_point",
                       rejected_reason="enter-before-leave order contradicted"),
            challenger("ch_0025b", "fam_0025", "partial_event",
                       "the person enters but never leaves within the clip",
                       "no_verified_match_at_operating_point"),
        ],
    ))
    F.append(family(
        "fam_0026", "sess_cross",
        "someone drops a bag then walks away",
        paras("a person puts a bag down and then leaves", "auth_2",
              "somebody sets a bag on the floor and walks off", "auth_3"),
        atoms_relations(
            [
                atom("a1", "someone", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "a bag", "object", True, "supported"),
                atom("a3", "drops", "action", True, "supported"),
                atom("a4", "walks away", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "places", "a2", True, "supported")],
            temporal_relations=[temporal_rel("a3", "before", "a4", max_gap=60, same_actor=True)],
        ),
        intervals("one", [interval("sess_cross", 520.0, 545.0, boundary(520.0, 545.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(520.0, 545.0)},
        ["temporal_order", "action"],
    ))
    F.append(family(
        "fam_0041", "sess_cross",
        "a person pushes a cart through the gate",
        paras("someone wheels a cart through the gate", "auth_1",
              "a person moves a trolley past the gate", "auth_3"),
        atoms_relations([
            atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "a cart", "object", True, "supported"),
            atom("a3", "pushes through the gate", "action", True, "supported"),
        ]),
        intervals("one", [interval("sess_cross", 700.0, 720.0, boundary(700.0, 720.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(700.0, 720.0)},
        ["action", "object"],
    ))

    # === sess_cross_b (staged/test) — temporal ===
    F.append(family(
        "fam_0027", "sess_cross_b",
        "a person leaves through the gate after putting down a box",
        paras("someone exits the gate after setting a box down", "auth_1",
              "a person departs via the gate having placed a box", "auth_2"),
        atoms_relations(
            [
                atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "a box", "object", True, "supported"),
                atom("a3", "puts down", "action", True, "supported"),
                atom("a4", "leaves through the gate", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "places", "a2", True, "supported")],
            temporal_relations=[temporal_rel("a3", "before", "a4", max_gap=120, same_actor=True)],
        ),
        intervals("one", [interval("sess_cross_b", 120.0, 210.0, boundary(120.0, 210.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(120.0, 210.0)},
        ["temporal_order", "action"],
        challengers=[
            challenger("ch_0027a", "fam_0027", "wrong_order",
                       "the person leaves and only afterwards a box appears",
                       "no_verified_match_at_operating_point"),
        ],
    ))
    F.append(family(
        "fam_0028", "sess_cross_b",
        "a vehicle stops and then a person exits it",
        paras("a car halts and someone gets out", "auth_3",
              "a vehicle comes to a stop and a person steps out", "auth_1"),
        atoms_relations(
            [
                atom("a1", "a vehicle", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "stops", "action", True, "supported"),
                atom("a3", "a person", "object", True, "supported"),
                atom("a4", "exits the vehicle", "action", True, "supported"),
            ],
            temporal_relations=[temporal_rel("a2", "before", "a4", max_gap=30, same_actor=False)],
        ),
        intervals("one", [interval("sess_cross_b", 400.0, 430.0, boundary(400.0, 430.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(400.0, 430.0)},
        ["temporal_order", "action"],
    ))

    # === sess_similar (staged/dev) — binding / similar actor ===
    F.append(family(
        "fam_0029", "sess_similar",
        "the person in the green cap picks up the parcel",
        paras("someone wearing a green cap lifts the parcel", "auth_1",
              "the green-capped person takes the parcel", "auth_2"),
        atoms_relations(
            [
                atom("a1", "person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "green cap", "attribute", True, "supported", vis=True),
                atom("a3", "the parcel", "object", True, "supported"),
                atom("a4", "picks up", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "picks_up", "a3", True, "supported")],
        ),
        intervals("one", [interval("sess_similar", 90.0, 104.0, boundary(90.0, 104.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(90.0, 104.0)},
        ["binding", "attribute", "action"],
        challengers=[
            challenger("ch_0029a", "fam_0029", "visually_similar_actor",
                       "a second person in a similar green cap who does NOT pick up the parcel",
                       "no_verified_match_at_operating_point",
                       rejected_reason="picks_up(green_cap_person, parcel) binding contradicted "
                                       "for the look-alike"),
        ],
    ))
    F.append(family(
        "fam_0030", "sess_similar",
        "a person in a yellow vest opens the door",
        paras("someone wearing a yellow hi-vis vest opens the door", "auth_2",
              "a yellow-vested person opens the door", "auth_3"),
        atoms_relations(
            [
                atom("a1", "person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "yellow vest", "attribute", True, "supported", vis=True),
                atom("a3", "the door", "object", True, "supported"),
                atom("a4", "opens", "action", True, "supported"),
            ],
            relations=[relation("r1", "a1", "near", "a3", True, "supported")],
        ),
        intervals("one", [interval("sess_similar", 260.0, 270.0, boundary(260.0, 270.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(260.0, 270.0)},
        ["binding", "attribute", "action"],
        challengers=[
            challenger("ch_0030a", "fam_0030", "wrong_binding",
                       "a person without a vest opens the door", "no_verified_match_at_operating_point"),
        ],
    ))

    # === sess_amb (staged/train) — ambiguous ===
    F.append(family(
        "fam_0031", "sess_amb",
        "maybe someone suspicious near the exit",
        paras("possibly a suspicious person by the exit", "auth_1",
              "perhaps somebody acting oddly at the exit", "auth_2"),
        atoms_relations([
            atom("a1", "someone", "object", True, role="candidate_anchor"),
            atom("a2", "suspicious", "attribute", True),
            atom("a3", "the exit", "location", True, role="filter"),
        ]),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["ambiguous"],
        empty_review=REVIEWED,
        notes="'suspicious' and 'maybe' are ambiguous -> the correct behaviour is one focused "
              "clarification (CLARIFICATION REQUIRED), not a fabricated verdict.",
    ))
    F.append(family(
        "fam_0032", "sess_amb",
        "a big bag somewhere",
        paras("some large bag in the scene", "auth_3", "a sizeable bag anywhere here", "auth_1"),
        atoms_relations([
            atom("a1", "a bag", "object", True, role="candidate_anchor"),
            atom("a2", "big", "attribute", True),
        ]),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["ambiguous"],
        empty_review=REVIEWED,
        notes="'big' is under-specified and 'somewhere' lacks scope -> clarification.",
    ))

    # === sess_longgap (staged/dev) — long-gap identity rejection ===
    F.append(family(
        "fam_0033", "sess_longgap",
        "the same person who entered at the start leaves at the end, twenty minutes later",
        paras("the identical individual from the opening returns to leave 20 minutes later", "auth_1",
              "prove the person leaving at the end is the one who entered at the start", "auth_2"),
        atoms_relations(
            [
                atom("a1", "a person", "object", True, role="candidate_anchor"),
                atom("a2", "enters at the start", "action", True),
                atom("a3", "leaves at the end", "action", True),
            ],
            temporal_relations=[temporal_rel("a2", "before", "a3", max_gap=1200, same_actor=True)],
        ),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["long_gap_identity_rejection", "temporal_order"],
        empty_review=REVIEWED,
        notes="Long-gap same_actor_required=true across a 20-minute discontinuity is UNSUPPORTED "
              "(§3.3). Correct behaviour: reject before retrieval and offer an order-only "
              "alternative (enter-event before leave-event, without an identity claim).",
    ))
    F.append(family(
        "fam_0034", "sess_longgap",
        "a person seen earlier appears again much later",
        paras("someone from earlier shows up again a long time afterwards", "auth_2",
              "the same person reappears after a long gap", "auth_3"),
        atoms_relations(
            [
                atom("a1", "a person", "object", True, role="candidate_anchor"),
                atom("a2", "seen earlier", "temporal", True),
                atom("a3", "appears again later", "temporal", True),
            ],
            temporal_relations=[temporal_rel("a2", "before", "a3", max_gap=900, same_actor=True)],
        ),
        intervals("zero", []),
        {"assessability": assess("assessable", ["clear"])},
        ["long_gap_identity_rejection"],
        empty_review=REVIEWED,
        notes="Cross-time identity across a long gap is unsupported -> clarification / order-only.",
    ))

    # === sess_org_a (organizer/train, authorization pending) ===
    F.append(family(
        "fam_0035", "sess_org_a",
        "a delivery truck at the dock",
        paras("a delivery lorry by the dock", "auth_1", "a goods truck at the loading dock", "auth_2"),
        atoms_relations([
            atom("a1", "delivery truck", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "the dock", "location", True, "supported", role="filter"),
        ]),
        intervals("one", [interval("sess_org_a", 80.0, 130.0, boundary(80.0, 130.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(80.0, 130.0)},
        ["object", "location"],
        notes="Organizer pool, authorization PENDING (§21.2). Synthetic placeholder until real "
              "organizer footage is delivered and a real manifest is sealed.",
    ))
    F.append(family(
        "fam_0036", "sess_org_a",
        "someone opens the side door",
        paras("a person opens the side entrance", "auth_3", "somebody opens the side doorway", "auth_1"),
        atoms_relations([
            atom("a1", "someone", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "the side door", "object", True, "supported"),
            atom("a3", "opens", "action", True, "supported"),
        ]),
        intervals("one", [interval("sess_org_a", 300.0, 312.0, boundary(300.0, 312.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(300.0, 312.0)},
        ["action", "object"],
        notes="Organizer pool, authorization PENDING.",
    ))

    # === sess_org_b (organizer/test, authorization pending) ===
    F.append(family(
        "fam_0037", "sess_org_b",
        "a person in a hi-vis jacket",
        paras("someone wearing a high-visibility jacket", "auth_1",
              "a person in a reflective hi-vis coat", "auth_2"),
        atoms_relations([
            atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
            atom("a2", "hi-vis jacket", "attribute", True, "supported", vis=True),
        ]),
        intervals("one", [interval("sess_org_b", 45.0, 60.0, boundary(45.0, 60.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(45.0, 60.0)},
        ["object", "attribute"],
        notes="Organizer pool, authorization PENDING.",
    ))
    F.append(family(
        "fam_0038", "sess_org_b",
        "a forklift moves a pallet then reverses",
        paras("a forklift carries a pallet and then backs up", "auth_2",
              "a forklift shifts a pallet and reverses afterward", "auth_3"),
        atoms_relations(
            [
                atom("a1", "a forklift", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "a pallet", "object", True, "supported"),
                atom("a3", "moves the pallet", "action", True, "supported"),
                atom("a4", "reverses", "action", True, "supported"),
            ],
            temporal_relations=[temporal_rel("a3", "before", "a4", max_gap=60, same_actor=True)],
        ),
        intervals("one", [interval("sess_org_b", 200.0, 235.0, boundary(200.0, 235.0))]),
        {"assessability": assess("assessable", ["clear"]), "boundary": boundary(200.0, 235.0)},
        ["temporal_order", "action"],
        notes="Organizer pool, authorization PENDING.",
    ))

    return F


# --------------------------------------------------------------------------- #
# Manifests
# --------------------------------------------------------------------------- #

def build_manifests() -> list[dict]:
    manifests = []
    for session, (pool, split, cam, dur, auth, consent) in SESSIONS.items():
        file_id = _video(session)
        m = {
            "manifest_schema_version": "1.1.0",
            "session_id": session,
            "scenario_id": _scenario(session),
            "pool": pool,
            "camera_ids": [_camera(session)],
            "footage_files": [{
                "file_id": file_id,
                "camera_id": _camera(session),
                "logical_path": f"footage/{session}/{file_id}.mp4",
                "source_sha256": S.sha256_hex(f"synthetic-source:{file_id}"),
                "duration_s": dur,
                "codec": "h264",
                "container": "mp4",
                "declared_fps": 25.0,
                "recording_start": None,
                "timebase": "1/25",
                "synthetic": True,
            }],
            "authorization": {
                "status": auth,
                "consent_recorded": consent,
                "retention_policy": "delete-after-event" if pool == "staged" else "per-organizer-agreement",
                "notes": "SYNTHETIC placeholder session." if pool == "staged"
                         else "SYNTHETIC placeholder; real organizer footage pending (§21.2).",
            },
            "provenance": {
                "collected_by": "member4-seed-generator",
                "collection_date": "2026-07-20",
                "staged_scenario_description": f"synthetic staged scenario for {_scenario(session)}"
                if pool == "staged" else "",
                "organizer_delivery_ref": None if pool == "staged" else "PENDING",
            },
            "synthetic": True,
            "notes": "Deterministic synthetic manifest generated by data/tools/build_seed_dataset.py.",
            "created_at": "2026-07-20T09:00:00",
        }
        manifests.append(S.seal_manifest(m))
    return manifests


# --------------------------------------------------------------------------- #
# Annotations: blind double annotation + adjudication for >=20% of families
# --------------------------------------------------------------------------- #

DOUBLE_ANNOTATED = ["fam_0001", "fam_0002", "fam_0008", "fam_0011", "fam_0014",
                    "fam_0017", "fam_0022", "fam_0025", "fam_0029", "fam_0033"]


def _present_absent(fam: dict) -> str:
    return "absent" if fam["ground_truth"]["intervals"]["cardinality"] == "zero" else "present"


def _atom_state_labels(fam: dict) -> list[dict]:
    out = []
    for a in fam["ground_truth"]["atoms_relations"]["atoms"]:
        if "gt_state" in a:
            out.append({"atom_id": a["atom_id"], "state": a["gt_state"]})
    return out


def _boundary_labels(fam: dict) -> list[dict]:
    out = []
    for iv in fam["ground_truth"]["intervals"]["intervals"]:
        out.append({"t0": iv["t0"], "t1": iv["t1"]})
    return out


def build_annotations(families: list[dict]) -> list[dict]:
    by_id = {f["family_id"]: f for f in families}
    records = []
    for fid in DOUBLE_ANNOTATED:
        fam = by_id[fid]
        pa = _present_absent(fam)
        atoms = _atom_state_labels(fam)
        bounds = _boundary_labels(fam)
        base_labels = {"present_absent": pa}
        if atoms:
            base_labels["atom_states"] = atoms
        if bounds:
            base_labels["temporal_boundaries"] = bounds

        a1 = {
            "annotation_id": f"ann_{fid}_A1",
            "target_type": "query_family", "target_id": fid,
            "annotator_id": "annotator_A1", "pass_type": "independent", "blind": True,
            "recorded_at": "2026-07-20T10:00:00", "labels": dict(base_labels), "synthetic": True,
        }
        a2 = {
            "annotation_id": f"ann_{fid}_A2",
            "target_type": "query_family", "target_id": fid,
            "annotator_id": "annotator_A2", "pass_type": "independent", "blind": True,
            "recorded_at": "2026-07-20T10:05:00", "labels": dict(base_labels), "synthetic": True,
        }
        adj = {
            "annotation_id": f"ann_{fid}_ADJ",
            "target_type": "query_family", "target_id": fid,
            "annotator_id": "adjudicator_J", "pass_type": "adjudication", "blind": False,
            "adjudicates": [a1["annotation_id"], a2["annotation_id"]],
            "recorded_at": "2026-07-21T09:00:00", "labels": dict(base_labels), "synthetic": True,
        }
        records += [a1, a2, adj]
    return records


# --------------------------------------------------------------------------- #
# Golden suite (§29): ten required queries
# --------------------------------------------------------------------------- #

def _golden_family(fid, canonical, para, ar, ivs, labels, tags,
                   empty_review=None, track_logic=None, notes=None):
    return family(fid, "sess_golden", canonical, para, ar, ivs, labels, tags,
                  empty_review=empty_review, track_logic=track_logic, notes=notes)


def build_golden_cases() -> list[dict]:
    G = []

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_01_object", "required_index": 1,
        "title": "Simple object", "synthetic": True,
        "family": _golden_family(
            "gold_fam_01",
            "a black backpack near the gate",
            paras("a dark backpack by the gate", "gold_a1", "a black rucksack at the gate", "gold_a2"),
            atoms_relations([
                atom("a1", "black backpack", "object", True, "supported", role="candidate_anchor", vis=True),
                atom("a2", "black", "attribute", True, "supported", vis=True),
                atom("a3", "the gate", "location", True, "supported", role="filter"),
            ]),
            intervals("one", [interval("sess_golden", 40.0, 52.0, boundary(40.0, 52.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(40.0, 52.0)},
            ["object", "attribute", "location"],
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_02_attribute", "required_index": 2,
        "title": "Attribute", "synthetic": True,
        "family": _golden_family(
            "gold_fam_02",
            "a person in a red jacket",
            paras("someone wearing a red coat", "gold_a1", "a person in a red jacket", "gold_a3"),
            atoms_relations([
                atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "red jacket", "attribute", True, "supported", vis=True),
            ]),
            intervals("one", [interval("sess_golden", 120.0, 133.0, boundary(120.0, 133.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(120.0, 133.0)},
            ["object", "attribute"],
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_03_action", "required_index": 3,
        "title": "Action", "synthetic": True,
        "family": _golden_family(
            "gold_fam_03",
            "a person places a bag on the ground",
            paras("someone sets a bag on the floor", "gold_a2", "a person puts a bag down", "gold_a1"),
            atoms_relations(
                [
                    atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                    atom("a2", "a bag", "object", True, "supported"),
                    atom("a3", "places on the ground", "action", True, "supported"),
                ],
                relations=[relation("r1", "a1", "places", "a2", True, "supported")],
            ),
            intervals("one", [interval("sess_golden", 210.0, 224.0, boundary(210.0, 224.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(210.0, 224.0)},
            ["object", "action"],
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_04_binding", "required_index": 4,
        "title": "Binding", "synthetic": True,
        "family": _golden_family(
            "gold_fam_04",
            "a person in red places a black bag near the gate and walks away",
            paras("a red-clothed person sets a black bag by the gate then leaves", "gold_a1",
                  "someone in red drops a black bag at the gate and departs", "gold_a2"),
            atoms_relations(
                [
                    atom("a1", "person", "object", True, "supported", role="candidate_anchor"),
                    atom("a2", "red", "attribute", True, "supported", vis=True),
                    atom("a3", "black bag", "object", True, "supported", vis=True),
                    atom("a4", "near the gate", "location", True, "supported", role="filter"),
                    atom("a5", "places", "action", True, "supported"),
                    atom("a6", "walks away", "action", True, "supported"),
                ],
                relations=[relation("r1", "a1", "places", "a3", True, "supported")],
                temporal_relations=[temporal_rel("a5", "before", "a6", max_gap=30, same_actor=True)],
            ),
            intervals("one", [interval("sess_golden", 300.0, 318.0, boundary(300.0, 318.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(300.0, 318.0)},
            ["binding", "attribute", "action", "temporal_order", "location"],
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_05_cross_window", "required_index": 5,
        "title": "Cross-window order", "synthetic": True,
        "family": _golden_family(
            "gold_fam_05",
            "a person enters near the gate, then later leaves carrying a bag",
            paras("someone comes in by the gate and afterwards exits with a bag", "gold_a1",
                  "a person arrives at the gate and later departs holding a bag", "gold_a3"),
            atoms_relations(
                [
                    atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                    atom("a2", "enters", "action", True, "supported"),
                    atom("a3", "near the gate", "location", True, "supported", role="filter"),
                    atom("a4", "leaves", "action", True, "supported"),
                    atom("a5", "a bag", "object", True, "supported"),
                    atom("a6", "carrying", "action", True, "supported"),
                ],
                relations=[relation("r1", "a1", "carries", "a5", True, "supported")],
                temporal_relations=[temporal_rel("a2", "before", "a4", max_gap=600, same_actor=True)],
            ),
            intervals("one", [interval("sess_golden", 360.0, 640.0, boundary(360.0, 640.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(360.0, 640.0)},
            ["temporal_order", "action", "binding", "location"],
            notes="Same-actor binding holds only within the visibly-trackable episode; no "
                  "long-gap identity is claimed.",
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_06_absent", "required_index": 6,
        "title": "Absent / no-match", "synthetic": True,
        "family": _golden_family(
            "gold_fam_06",
            "a person carrying a yellow umbrella",
            paras("someone with a yellow umbrella", "gold_a2", "a person holding a yellow brolly", "gold_a1"),
            atoms_relations([
                atom("a1", "a person", "object", True, role="candidate_anchor"),
                atom("a2", "yellow umbrella", "object", True, vis=True),
            ]),
            intervals("zero", []),
            {"assessability": assess("assessable", ["clear"])},
            ["empty_set"],
            empty_review=REVIEWED,
            notes="Reviewed full scope; no such event.",
        ),
        "expected": {"archive_conclusion": "no_verified_match_at_operating_point",
                     "headline_contains": "NO VERIFIED MATCH"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_07_unobservable", "required_index": 7,
        "title": "Unobservable", "synthetic": True,
        "family": _golden_family(
            "gold_fam_07",
            "the colour of the bag in the dark corner",
            paras("what colour is the bag in the dark corner", "gold_a1",
                  "identify the bag colour in the shadowed corner", "gold_a2"),
            atoms_relations([
                atom("a1", "the bag", "object", True, "supported", role="candidate_anchor"),
                atom("a2", "bag colour", "attribute", True, "unobservable", reason="low_light", vis=True),
                atom("a3", "the dark corner", "location", True, "supported", role="filter"),
            ]),
            intervals("zero", []),
            {"assessability": assess("partially_assessable", ["low_light", "dark"])},
            ["unobservable"],
            empty_review=REVIEWED,
            notes="A bag is present but its colour is unassessable in the dark -> the required "
                  "attribute is unobservable.",
        ),
        "expected": {"archive_conclusion": "insufficient_visual_evidence",
                     "headline_contains": "INSUFFICIENT VISUAL EVIDENCE"},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_08_disjunction", "required_index": 8,
        "title": "Bounded disjunction", "synthetic": True,
        "family": _golden_family(
            "gold_fam_08",
            "a person wearing a red or blue hat",
            paras("someone in a red or blue cap", "gold_a1", "a person whose hat is red or blue", "gold_a3"),
            atoms_relations(
                [
                    atom("a1", "a person", "object", True, "supported", role="candidate_anchor"),
                    atom("a2", "red hat", "attribute", False, "contradicted", vis=True),
                    atom("a3", "blue hat", "attribute", False, "supported", vis=True),
                ],
                logic_groups=[logic_group("g1", "any", ["a2", "a3"], "candidate_episode", "satisfied")],
            ),
            intervals("one", [interval("sess_golden", 700.0, 713.0, boundary(700.0, 713.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(700.0, 713.0)},
            ["bounded_or", "attribute"],
            track_logic={"disjunction_gt": [{
                "group_id": "g1", "alternatives": ["red hat", "blue hat"],
                "present_alternatives": ["blue hat"], "expected_outcome": "satisfied",
            }]},
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH",
                     "variants": [
                         {"name": "blue_hat_present", "expected_outcome": "satisfied"},
                         {"name": "green_hat_only", "expected_outcome": "not_satisfied",
                          "note": "neither alternative present -> no verified match"},
                     ]},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_09_visible_absence", "required_index": 9,
        "title": "Visible absence (assessable + occluded variants)", "synthetic": True,
        "family": _golden_family(
            "gold_fam_09",
            "no bag left in the corridor",
            paras("the corridor has no bag left in it", "gold_a2",
                  "there is no bag anywhere in the corridor", "gold_a1"),
            atoms_relations(
                [atom("a1", "a bag", "object", True, "contradicted", role="candidate_anchor", vis=True)],
                logic_groups=[logic_group("g_assess", "visible_none", ["a1"], "candidate_episode",
                                          "not_satisfied")],
            ),
            intervals("zero", []),
            {"assessability": assess("assessable", ["clear"])},
            ["visible_absence", "empty_set"],
            empty_review=REVIEWED,
            track_logic={"visible_none_gt": [
                {"group_id": "g_assess", "target": "bag",
                 "observation_interval": obs_interval("sess_golden", 0.0, 120.0),
                 "assessable": True, "expected_observation_ticks": 120,
                 "observed_ticks_complete": True,
                 "expected_outcome": "visible_absence_supported"},
                {"group_id": "g_occluded", "target": "bag",
                 "observation_interval": obs_interval("sess_golden", 120.0, 240.0),
                 "assessable": False, "expected_observation_ticks": 120,
                 "observed_ticks_complete": False,
                 "expected_outcome": "unobservable"},
            ]},
            notes="Two variants recorded together: an assessable stretch where absence is "
                  "certifiable, and an occluded stretch where it is not.",
        ),
        "expected": {"archive_conclusion": "no_verified_match_at_operating_point",
                     "headline_contains": "NO VERIFIED MATCH",
                     "variants": [
                         {"name": "assessable", "expected_outcome": "visible_absence_supported",
                          "note": "clean negative certifiable"},
                         {"name": "occluded", "expected_outcome": "unobservable",
                          "note": "missing coverage -> cannot certify absence -> INSUFFICIENT VISUAL EVIDENCE"},
                     ]},
    })

    G.append({
        "golden_case_version": "1.0.0", "case_id": "golden_10_bounded_count", "required_index": 10,
        "title": "Bounded count with fragmentation decoy", "synthetic": True,
        "family": _golden_family(
            "gold_fam_10",
            "how many people are waiting by the bench",
            paras("count the people waiting at the bench", "gold_a1",
                  "number of people standing by the bench", "gold_a2"),
            atoms_relations(
                [atom("a1", "people waiting", "object", True, "supported", role="candidate_anchor"),
                 atom("a2", "the bench", "location", True, "supported", role="filter")],
                logic_groups=[logic_group("g_clean", "count", ["a1"], "continuous_camera_interval",
                                          "satisfied", min_c=1, max_c=5)],
            ),
            intervals("one", [interval("sess_golden", 780.0, 860.0, boundary(780.0, 860.0))]),
            {"assessability": assess("assessable", ["clear"]), "boundary": boundary(780.0, 860.0)},
            ["bounded_count", "location"],
            track_logic={"count_gt": [
                {"group_id": "g_clean",
                 "continuous_camera_interval": obs_interval("sess_golden", 780.0, 860.0),
                 "qualifying_tracklets": 2, "declared_bound": 5,
                 "fragmentation_level": "none", "occlusion_level": "none",
                 "expected_outcome": 2},
                {"group_id": "g_decoy",
                 "continuous_camera_interval": obs_interval("sess_golden", 860.0, 900.0),
                 "qualifying_tracklets": 2, "declared_bound": 5,
                 "fragmentation_level": "high", "occlusion_level": "heavy",
                 "expected_outcome": "unresolved"},
            ]},
            notes="Clean interval yields an exact count of 2; the fragmentation/occlusion decoy "
                  "MUST return 'unresolved', never an inflated count.",
        ),
        "expected": {"archive_conclusion": "verified_matches_found",
                     "headline_contains": "VERIFIED MATCH",
                     "variants": [
                         {"name": "clean_interval", "expected_outcome": "2"},
                         {"name": "fragmentation_decoy", "expected_outcome": "unresolved",
                          "note": "track fragmentation + heavy occlusion -> unresolved"},
                     ]},
    })

    return G


# --------------------------------------------------------------------------- #
# Write + validate
# --------------------------------------------------------------------------- #

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _validate_all(manifests, families, annotations, golden) -> list[str]:
    errors: list[str] = []
    for m in manifests:
        r = S.validate_footage_manifest(m)
        errors += [f"manifest {m['session_id']}: {e}" for e in r.errors]
    for f in families:
        r = S.validate_query_family(f)
        errors += [f"family {f['family_id']}: {e}" for e in r.errors]
    for a in annotations:
        r = S.validate_annotation_record(a)
        errors += [f"annotation {a['annotation_id']}: {e}" for e in r.errors]
    for g in golden:
        r = S.validate_golden_case(g)
        errors += [f"golden {g['case_id']}: {e}" for e in r.errors]
    errors += S.check_split_discipline(families).errors
    errors += S.check_annotation_ordering(annotations).errors
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic seed dataset (Member 4).")
    parser.add_argument("--check", action="store_true",
                        help="Validate in memory without writing files.")
    args = parser.parse_args(argv)

    manifests = build_manifests()
    families = build_families()
    annotations = build_annotations(families)
    golden = build_golden_cases()

    errors = _validate_all(manifests, families, annotations, golden)
    if errors:
        print(f"VALIDATION FAILED with {len(errors)} error(s):")
        for e in errors[:60]:
            print(f"  - {e}")
        return 1

    da_frac = S.double_annotation_fraction([f["family_id"] for f in families], annotations)
    print("Validation passed.")
    print(f"  manifests:   {len(manifests)}")
    print(f"  families:    {len(families)}  (minimum 40; target 60-80)")
    print(f"  annotations: {len(annotations)}  (double-annotation fraction {da_frac:.3f})")
    print(f"  golden:      {len(golden)}  (§29 requires 10)")

    if args.check:
        print("--check: not writing files.")
        return 0

    for m in manifests:
        _write_json(MANIFESTS_DIR / f"{m['session_id']}.json", m)
    for f in families:
        _write_json(FAMILIES_DIR / f"{f['family_id']}.json", f)
    for a in annotations:
        sub = "adjudication" if a["pass_type"] == "adjudication" else "independent"
        _write_json(ANNOTATIONS_DIR / sub / f"{a['annotation_id']}.json", a)
    for g in golden:
        _write_json(GOLDEN_DIR / f"{g['case_id']}.json", g)

    print("Wrote seed dataset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
