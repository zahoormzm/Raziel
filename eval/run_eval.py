"""Evaluation orchestrator / CLI.

Loads the dataset, validates it (schema + split discipline + immutability +
annotation ordering), scores every configuration for which predictions were
supplied, and emits the machine-readable benchmark panel plus its §22.4 text
block. With no ``--predictions`` supplied, the panel is fully
``not_yet_measured`` but still carries the config hash, evaluation date, dataset
summary, and data-integrity report. It never fabricates numbers.

Usage::

    python -m eval.run_eval                          # dataset report, all not_yet_measured
    python -m eval.run_eval --predictions FULL.json B1.json --out panel.json --report panel.txt
    python -m eval.run_eval --strict                 # non-zero exit if the dataset is invalid

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §22, §26.1.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

from . import baselines, panel, schema
from .schema import (DATA_DIR, REPO_ROOT, NOT_YET_MEASURED, check_annotation_ordering,
                     check_split_discipline, double_annotation_fraction, load_json,
                     verify_manifest_hash)

PLAN_VERSION = "v1.3"

FAMILIES_DIR = DATA_DIR / "queries" / "families"
MANIFESTS_DIR = DATA_DIR / "manifests"
ANNOTATIONS_DIR = DATA_DIR / "annotations"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "suite"

DEFAULT_IOU_THRESHOLD = 0.5          # §22.1(2), declared
DEFAULT_CANDIDATE_IOU_THRESHOLD = 0.0  # candidate recall: any positive overlap (§14.4)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _load_dir(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [load_json(p) for p in sorted(path.glob("*.json"))]


def load_families() -> list[dict]:
    return _load_dir(FAMILIES_DIR)


def load_manifests() -> list[dict]:
    return _load_dir(MANIFESTS_DIR)


def load_annotations() -> list[dict]:
    records: list[dict] = []
    if ANNOTATIONS_DIR.exists():
        for p in sorted(ANNOTATIONS_DIR.rglob("*.json")):
            records.append(load_json(p))
    return records


# --------------------------------------------------------------------------- #
# Validation / integrity
# --------------------------------------------------------------------------- #

def validate_dataset(families: list[dict], manifests: list[dict],
                     annotations: list[dict]) -> dict:
    """Return an integrity report; ``ok`` is True only if everything passes."""
    errors: list[str] = []

    for fam in families:
        r = schema.validate_query_family(fam)
        if not r.ok:
            errors += [f"family {fam.get('family_id','?')}: {e}" for e in r.errors]

    for man in manifests:
        r = schema.validate_footage_manifest(man)
        if not r.ok:
            errors += [f"manifest {man.get('session_id','?')}: {e}" for e in r.errors]

    for rec in annotations:
        r = schema.validate_annotation_record(rec)
        if not r.ok:
            errors += [f"annotation {rec.get('annotation_id','?')}: {e}" for e in r.errors]

    split = check_split_discipline(families)
    ordering = check_annotation_ordering(annotations)
    manifests_immutable = all(verify_manifest_hash(m) for m in manifests) if manifests else True
    family_ids = [f.get("family_id") for f in families]
    da_frac = double_annotation_fraction(family_ids, annotations)

    return {
        "ok": bool(not errors and split.ok and ordering.ok and manifests_immutable),
        "schema_errors": errors,
        "split_discipline": {"ok": split.ok, "errors": split.errors},
        "annotation_ordering": {"ok": ordering.ok, "errors": ordering.errors},
        "manifests_immutable": manifests_immutable,
        "double_annotation_fraction": da_frac,
        "double_annotation_meets_20pct": da_frac >= 0.20,
        "counts": {"families": len(families), "manifests": len(manifests),
                   "annotations": len(annotations)},
    }


# --------------------------------------------------------------------------- #
# Dataset summary (feeds the dataset card + panel)
# --------------------------------------------------------------------------- #

def build_dataset_summary(families: list[dict], manifests: list[dict],
                          annotations: list[dict]) -> dict:
    by_pool: dict[str, int] = {}
    by_split: dict[str, int] = {}
    capability: dict[str, int] = {}
    challengers: dict[str, int] = {}
    cardinality: dict[str, int] = {}
    scenarios: set = set()
    sessions: set = set()
    synthetic = 0

    for fam in families:
        by_pool[fam.get("pool")] = by_pool.get(fam.get("pool"), 0) + 1
        by_split[fam.get("split")] = by_split.get(fam.get("split"), 0) + 1
        scenarios.add(fam.get("scenario_id"))
        for s in fam.get("session_ids", []):
            sessions.add(s)
        for tag in fam.get("capability_tags", []):
            capability[tag] = capability.get(tag, 0) + 1
        card = fam.get("ground_truth", {}).get("intervals", {}).get("cardinality")
        cardinality[card] = cardinality.get(card, 0) + 1
        for ch in fam.get("challengers", []):
            challengers[ch.get("type")] = challengers.get(ch.get("type"), 0) + 1
        if fam.get("synthetic"):
            synthetic += 1

    return {
        "family_count": len(families),
        "minimum_required": 40,
        "target_range": [60, 80],
        "by_pool": by_pool,
        "by_split": by_split,
        "cardinality_distribution": cardinality,
        "capability_coverage": dict(sorted(capability.items())),
        "challenger_distribution": dict(sorted(challengers.items())),
        "scenario_count": len(scenarios),
        "session_count": len(sessions),
        "manifest_count": len(manifests),
        "annotation_count": len(annotations),
        "paraphrases_per_family": 2,
        "synthetic_family_count": synthetic,
        "all_synthetic": synthetic == len(families) and len(families) > 0,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def load_prediction_bundles(paths: list[str]) -> dict[str, baselines.PredictionsBundle]:
    bundles: dict[str, baselines.PredictionsBundle] = {}
    for p in paths or []:
        obj = load_json(p)
        b = baselines.PredictionsBundle.from_dict(obj)
        bundles[b.config] = b
    return bundles


def run(predictions_paths: Optional[list[str]] = None,
        iou_threshold: float = DEFAULT_IOU_THRESHOLD,
        candidate_iou_threshold: float = DEFAULT_CANDIDATE_IOU_THRESHOLD,
        primary_config: str = "FULL",
        evaluated_date: Optional[str] = None) -> dict:
    families = load_families()
    manifests = load_manifests()
    annotations = load_annotations()

    integrity = validate_dataset(families, manifests, annotations)
    summary = build_dataset_summary(families, manifests, annotations)
    bundles = load_prediction_bundles(predictions_paths or [])

    sections: dict[str, dict] = {}
    for name in baselines.BASELINE_ORDER:
        config = baselines.BASELINES[name]
        bundle = bundles.get(name)
        sections[name] = baselines.compute_config_metrics(
            families, bundle, config,
            iou_threshold=iou_threshold,
            candidate_iou_threshold=candidate_iou_threshold,
        )

    manifest_hashes = sorted(m.get("content_hash", "") for m in manifests)
    eval_config_inputs = {
        "plan_version": PLAN_VERSION,
        "iou_threshold": iou_threshold,
        "candidate_iou_threshold": candidate_iou_threshold,
        "budget": {
            "candidate_clusters_max": baselines.DEFAULT_DECLARED_BUDGET.candidate_clusters_max,
            "verification_clusters_max": baselines.DEFAULT_DECLARED_BUDGET.verification_clusters_max,
            "verification_seconds_max": baselines.DEFAULT_DECLARED_BUDGET.verification_seconds_max,
        },
        "schema_version": schema.registry()._docs["query_family.schema.json"].get("$id"),
        "dataset_manifest_hashes": manifest_hashes,
        "family_count": len(families),
    }

    return panel.build_panel(
        summary, sections,
        eval_config_inputs=eval_config_inputs,
        evaluated_date=evaluated_date or _dt.date.today().isoformat(),
        iou_threshold=iou_threshold,
        primary_config=primary_config,
        integrity=integrity,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RAZIEL evaluation orchestrator (Member 4).")
    parser.add_argument("--predictions", nargs="*", default=[],
                        help="Prediction bundle JSON files (one per config). Omit for a "
                             "dataset-only report with all metrics not_yet_measured.")
    parser.add_argument("--out", default=None, help="Write the panel JSON here.")
    parser.add_argument("--report", default=None, help="Write the §22.4 text block here.")
    parser.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
    parser.add_argument("--primary-config", default="FULL", choices=baselines.BASELINE_ORDER)
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero if the dataset fails validation.")
    args = parser.parse_args(argv)

    result = run(predictions_paths=args.predictions,
                 iou_threshold=args.iou_threshold,
                 primary_config=args.primary_config)

    text = panel.render_text(result)
    integrity = result["data_integrity"]

    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")

    print(text)
    print("\n--- data integrity ---")
    print(f"valid: {integrity['ok']}")
    print(f"families: {integrity['counts']['families']}  "
          f"manifests: {integrity['counts']['manifests']}  "
          f"annotations: {integrity['counts']['annotations']}")
    print(f"split discipline ok: {integrity['split_discipline']['ok']}")
    print(f"manifests immutable: {integrity['manifests_immutable']}")
    print(f"double-annotation fraction: {integrity['double_annotation_fraction']:.3f} "
          f"(>=20%: {integrity['double_annotation_meets_20pct']})")
    if not integrity["ok"]:
        for e in integrity["schema_errors"][:20]:
            print(f"  ERROR: {e}")
        for e in integrity["split_discipline"]["errors"][:20]:
            print(f"  SPLIT: {e}")
        for e in integrity["annotation_ordering"]["errors"][:20]:
            print(f"  ANNOTATION: {e}")

    if args.strict and not integrity["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
