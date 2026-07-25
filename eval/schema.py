"""Dataset schema loading, validation, canonical hashing, and split discipline.

Stdlib-only (no ``jsonschema``/``PyYAML``), honouring the §8.4 offline rule. A
small JSON-Schema (draft 2020-12 subset) validator drives structural checks from
the versioned files in ``data/schemas/`` so those files are the single source of
truth. Cross-field rules that JSON Schema cannot express (immutability, split
leakage, retriever-truth prohibition, visible_none / count safety, adjudication
ordering) are enforced here and covered by the owned test suite.

Authority: RAZIEL_Master_Execution_Plan_v1.3.md §21.5, §21.6, §14.3, §22.1, §26.1.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
SCHEMA_DIR = DATA_DIR / "schemas"

# --------------------------------------------------------------------------- #
# Frozen vocabularies (RAZIEL_Master_Execution_Plan_v1.3.md §4.1, §13.1, §21.4)
# --------------------------------------------------------------------------- #

#: Verifier / system output states (§4.1). ``undetermined`` = the system could not decide.
EVIDENCE_STATES = ("supported", "contradicted", "unobservable", "undetermined")

#: Ground-truth label states. Excludes ``undetermined`` — it is never a label (§22.1(4)).
GROUND_TRUTH_STATES = ("supported", "contradicted", "unobservable")

#: Bounded logical algebra (§2.2, §13.1). No open-world negation / universals / comparisons.
LOGIC_OPERATORS = ("all", "any", "visible_none", "count")

RELATION_PREDICATES = ("carries", "near", "wears", "places", "picks_up", "follows")

CHALLENGER_TYPES = (
    "wrong_attribute", "wrong_object", "wrong_binding", "wrong_order",
    "partial_event", "short_interruption", "visually_similar_actor",
    "unobservable", "true_no_event", "repeated_events",
    "track_fragmentation", "duplicate_track", "bounded_disjunction",
    "visible_absence_assessable", "visible_absence_unassessable",
    "bounded_count_correct", "bounded_count_incorrect",
)

ARCHIVE_CONCLUSIONS = (
    "verified_matches_found", "no_verified_match_at_operating_point",
    "insufficient_visual_evidence", "search_incomplete", "not_applicable",
)

#: Sentinel distinguishing "not yet measured" from a numeric result (§22.4). A
#: benchmark-panel field is always either this exact string or a real number —
#: never a fabricated stand-in.
NOT_YET_MEASURED = "not_yet_measured"


# --------------------------------------------------------------------------- #
# Canonical JSON + content hashing (immutability, §20.3 canonical-hash pattern)
# --------------------------------------------------------------------------- #

def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used for hashing and comparison."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(obj: dict, omit_field: str = "content_hash") -> str:
    """SHA-256 over canonical JSON with ``omit_field`` removed.

    Mirrors the manifest-hash rule in §20.3: hash the object with its own hash
    field omitted so the hash certifies everything else.
    """
    clone = {k: v for k, v in obj.items() if k != omit_field}
    return sha256_hex(canonical_json(clone))


def seal_manifest(manifest: dict) -> dict:
    """Return a copy of ``manifest`` with a freshly computed ``content_hash``."""
    sealed = dict(manifest)
    sealed["content_hash"] = content_hash(sealed)
    return sealed


def verify_manifest_hash(manifest: dict) -> bool:
    """True iff the stored ``content_hash`` matches the recomputed one."""
    stored = manifest.get("content_hash")
    return isinstance(stored, str) and stored == content_hash(manifest)


# --------------------------------------------------------------------------- #
# Minimal JSON-Schema validator (draft 2020-12 subset)
# --------------------------------------------------------------------------- #

def _json_type_ok(instance: Any, t: str) -> bool:
    if t == "boolean":
        return isinstance(instance, bool)
    if t == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if t == "string":
        return isinstance(instance, str)
    if t == "object":
        return isinstance(instance, dict)
    if t == "array":
        return isinstance(instance, list)
    if t == "null":
        return instance is None
    return False


class SchemaRegistry:
    """Loads and caches the versioned schema files and validates against them."""

    def __init__(self, schema_dir: Path = SCHEMA_DIR):
        self.schema_dir = Path(schema_dir)
        self._docs: dict[str, dict] = {}
        for path in sorted(self.schema_dir.glob("*.schema.json")):
            self._docs[path.name] = json.loads(path.read_text(encoding="utf-8"))

    # -- $ref resolution ---------------------------------------------------- #
    def _resolve_ref(self, ref: str, current_doc: dict) -> tuple[dict, dict]:
        """Return (target_subschema, target_root_doc) for a $ref string."""
        if ref.startswith("#"):
            return self._pointer(current_doc, ref[1:]), current_doc
        if "#" in ref:
            filename, pointer = ref.split("#", 1)
        else:
            filename, pointer = ref, ""
        if filename not in self._docs:
            raise KeyError(f"unknown schema referenced: {filename!r}")
        doc = self._docs[filename]
        return (self._pointer(doc, pointer) if pointer else doc), doc

    @staticmethod
    def _pointer(doc: dict, pointer: str) -> dict:
        node: Any = doc
        for raw in pointer.split("/"):
            if raw == "":
                continue
            token = raw.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    # -- validation --------------------------------------------------------- #
    def validate(self, instance: Any, schema_filename: str) -> list[str]:
        doc = self._docs[schema_filename]
        errors: list[str] = []
        self._validate(instance, doc, doc, "$", errors)
        return errors

    def _validate(self, instance: Any, schema: dict, doc: dict, path: str,
                  errors: list[str]) -> None:
        if "$ref" in schema:
            target, target_doc = self._resolve_ref(schema["$ref"], doc)
            self._validate(instance, target, target_doc, path, errors)
            return

        if "const" in schema and instance != schema["const"]:
            errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

        if "type" in schema:
            types = schema["type"]
            types = [types] if isinstance(types, str) else types
            if not any(_json_type_ok(instance, t) for t in types):
                errors.append(f"{path}: {instance!r} is not of type {types}")

        for combiner in ("oneOf", "anyOf"):
            if combiner in schema:
                passes = 0
                for sub in schema[combiner]:
                    sub_errs: list[str] = []
                    self._validate(instance, sub, doc, path, sub_errs)
                    if not sub_errs:
                        passes += 1
                if combiner == "oneOf" and passes != 1:
                    errors.append(f"{path}: matched {passes} oneOf branches (need exactly 1)")
                if combiner == "anyOf" and passes == 0:
                    errors.append(f"{path}: matched no anyOf branch")

        if isinstance(instance, dict):
            self._validate_object(instance, schema, doc, path, errors)
        elif isinstance(instance, list):
            self._validate_array(instance, schema, doc, path, errors)
        elif isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                errors.append(f"{path}: string shorter than minLength {schema['minLength']}")
            if "pattern" in schema and not re.search(schema["pattern"], instance):
                errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")
        elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    def _validate_object(self, instance: dict, schema: dict, doc: dict, path: str,
                         errors: list[str]) -> None:
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            sub_path = f"{path}.{key}"
            if key in props:
                self._validate(value, props[key], doc, sub_path, errors)
            elif additional is False:
                errors.append(f"{path}: additional property {key!r} not allowed")
            elif isinstance(additional, dict):
                self._validate(value, additional, doc, sub_path, errors)

    def _validate_array(self, instance: list, schema: dict, doc: dict, path: str,
                        errors: list[str]) -> None:
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array shorter than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: array longer than maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(instance):
                self._validate(item, item_schema, doc, f"{path}[{i}]", errors)


# Shared default registry (lazy).
_REGISTRY: SchemaRegistry | None = None


def registry() -> SchemaRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SchemaRegistry()
    return _REGISTRY


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class ValidationResult:
    """Outcome of validating one artifact."""
    ok: bool
    errors: list[str] = field(default_factory=list)

    def raise_for_errors(self) -> None:
        if not self.ok:
            raise ValueError("; ".join(self.errors))


def _result(errors: list[str]) -> ValidationResult:
    return ValidationResult(ok=not errors, errors=errors)


# --------------------------------------------------------------------------- #
# Domain (cross-field) validation
# --------------------------------------------------------------------------- #

def validate_footage_manifest(manifest: dict) -> ValidationResult:
    errors = registry().validate(manifest, "footage_session_manifest.schema.json")
    if "content_hash" in manifest and not verify_manifest_hash(manifest):
        errors.append(
            f"content_hash mismatch: manifest is not immutable/intact "
            f"(stored {manifest.get('content_hash')!r}, computed {content_hash(manifest)!r})"
        )
    return _result(errors)


def validate_ledger_entry(entry: dict) -> ValidationResult:
    errors = registry().validate(entry, "ledger_entry.schema.json")
    if _num(entry.get("end_pts")) < _num(entry.get("start_pts")):
        errors.append("ledger_entry: end_pts < start_pts")
    # provenance.source const is enforced structurally; re-assert defensively.
    if entry.get("provenance", {}).get("source") != "human_watch":
        errors.append("ledger_entry: provenance.source must be 'human_watch' (no retriever truth)")
    return _result(errors)


def validate_interval_block(block: dict) -> ValidationResult:
    errors = registry().validate(block, "interval.schema.json")
    errors.extend(_interval_cardinality_errors(block))
    return _result(errors)


def _interval_cardinality_errors(block: dict) -> list[str]:
    errors: list[str] = []
    intervals = block.get("intervals", [])
    cardinality = block.get("cardinality")
    n = len(intervals)
    expect = {"zero": n == 0, "one": n == 1, "many": n >= 2}
    if cardinality in expect and not expect[cardinality]:
        errors.append(f"interval: cardinality {cardinality!r} inconsistent with {n} interval(s)")
    for i, iv in enumerate(intervals):
        if _num(iv.get("t1")) < _num(iv.get("t0")):
            errors.append(f"interval[{i}]: t1 < t0")
    return errors


def validate_track_logic(tl: dict) -> ValidationResult:
    errors = registry().validate(tl, "track_logic_ground_truth.schema.json")
    errors.extend(_track_logic_safety_errors(tl))
    return _result(errors)


def _track_logic_safety_errors(tl: dict) -> list[str]:
    """visible_none and count safety rules (§14.3): missing coverage / unsafe
    fragmentation-occlusion can never become a clean negative or a hard count."""
    errors: list[str] = []
    for vn in tl.get("visible_none_gt", []):
        gid = vn.get("group_id")
        outcome = vn.get("expected_outcome")
        certifiable = bool(vn.get("assessable")) and bool(vn.get("observed_ticks_complete"))
        if outcome == "visible_absence_supported" and not certifiable:
            errors.append(
                f"visible_none[{gid}]: visible_absence_supported requires assessable AND "
                f"observed_ticks_complete (missing coverage cannot be a clean negative)"
            )
    for c in tl.get("count_gt", []):
        gid = c.get("group_id")
        outcome = c.get("expected_outcome")
        frag = c.get("fragmentation_level")
        occ = c.get("occlusion_level")
        bound = c.get("declared_bound")
        if isinstance(outcome, int) and not isinstance(outcome, bool):
            if frag == "high" or occ == "heavy":
                errors.append(
                    f"count[{gid}]: integer outcome illegal under fragmentation={frag}/"
                    f"occlusion={occ} (must be 'unresolved')"
                )
            if isinstance(bound, int) and outcome > bound:
                errors.append(f"count[{gid}]: outcome {outcome} exceeds declared_bound {bound}")
    return errors


def validate_challenger(ch: dict) -> ValidationResult:
    return _result(registry().validate(ch, "challenger.schema.json"))


def validate_query_family(family: dict) -> ValidationResult:
    errors = registry().validate(family, "query_family.schema.json")

    # Paraphrase independence: exactly two, distinct authors.
    paras = family.get("paraphrases", [])
    authors = [p.get("author_id") for p in paras]
    if len(paras) == 2 and authors[0] == authors[1]:
        errors.append("query_family: the two paraphrases must have distinct author_id")

    # Ground-truth source must be human (retriever truth prohibited, §21.5).
    if family.get("ground_truth_source") != "human_ledger":
        errors.append("query_family: ground_truth_source must be 'human_ledger'")

    gt = family.get("ground_truth", {})
    intervals = gt.get("intervals", {})
    errors.extend(_interval_cardinality_errors(intervals))

    # Empty-set review discipline.
    cardinality = intervals.get("cardinality")
    review = family.get("empty_set_review", {})
    if cardinality == "zero":
        if not review.get("required"):
            errors.append("query_family: cardinality=zero requires empty_set_review.required=true")
        if family.get("split") == "test" and not review.get("review_complete"):
            errors.append(
                "query_family: empty-set test family needs full human review "
                "(empty_set_review.review_complete=true) before use as test truth"
            )

    # Track/logic safety (if present).
    if "track_logic" in gt:
        errors.extend(_track_logic_safety_errors(gt["track_logic"]))

    # Challenger back-references.
    for i, ch in enumerate(family.get("challengers", [])):
        if ch.get("family_id") not in (None, family.get("family_id")):
            errors.append(f"query_family.challengers[{i}]: family_id mismatch")

    return _result(errors)


def validate_annotation_record(record: dict) -> ValidationResult:
    return _result(registry().validate(record, "annotation_record.schema.json"))


def validate_golden_case(case: dict) -> ValidationResult:
    errors = registry().validate(case, "golden_case.schema.json")
    fam = case.get("family")
    if isinstance(fam, dict):
        errors.extend(validate_query_family(fam).errors)
    return _result(errors)


# --------------------------------------------------------------------------- #
# Split discipline (§21.5) — applied across a set of families
# --------------------------------------------------------------------------- #

def check_split_discipline(families: list[dict]) -> ValidationResult:
    """Split by scenario/session; keep staged, organizer, and external pools separate."""
    errors: list[str] = []
    scenario_split: dict[str, str] = {}
    scenario_pool: dict[str, str] = {}
    session_split: dict[str, str] = {}

    for fam in families:
        fid = fam.get("family_id", "?")
        scenario = fam.get("scenario_id")
        split = fam.get("split")
        pool = fam.get("pool")

        if scenario in scenario_split and scenario_split[scenario] != split:
            errors.append(
                f"split leakage: scenario {scenario!r} spans splits "
                f"{scenario_split[scenario]!r} and {split!r} (family {fid})"
            )
        else:
            scenario_split.setdefault(scenario, split)

        if scenario in scenario_pool and scenario_pool[scenario] != pool:
            errors.append(
                f"pool bleed: scenario {scenario!r} appears in pools "
                f"{scenario_pool[scenario]!r} and {pool!r} (family {fid})"
            )
        else:
            scenario_pool.setdefault(scenario, pool)

        for session in fam.get("session_ids", []):
            if session in session_split and session_split[session] != split:
                errors.append(
                    f"split leakage: session {session!r} spans splits "
                    f"{session_split[session]!r} and {split!r} (family {fid})"
                )
            else:
                session_split.setdefault(session, split)

    return _result(errors)


# --------------------------------------------------------------------------- #
# Adjudication ordering (§21.6)
# --------------------------------------------------------------------------- #

def check_annotation_ordering(records: list[dict]) -> ValidationResult:
    """Adjudication only after two independent, blind labels are recorded."""
    errors: list[str] = []
    by_id = {r.get("annotation_id"): r for r in records}
    independent_by_target: dict[str, list[dict]] = {}
    for r in records:
        if r.get("pass_type") == "independent":
            independent_by_target.setdefault(r.get("target_id"), []).append(r)

    for r in records:
        if r.get("pass_type") != "adjudication":
            continue
        aid = r.get("annotation_id")
        refs = r.get("adjudicates", [])
        if len(refs) != 2:
            errors.append(f"adjudication {aid}: must reference exactly two independent passes")
            continue
        adj_time = _parse_ts(r.get("recorded_at"))
        for ref in refs:
            src = by_id.get(ref)
            if src is None:
                errors.append(f"adjudication {aid}: references unknown annotation {ref!r}")
                continue
            if src.get("pass_type") != "independent":
                errors.append(f"adjudication {aid}: {ref!r} is not an independent pass")
            src_time = _parse_ts(src.get("recorded_at"))
            if adj_time is not None and src_time is not None and adj_time <= src_time:
                errors.append(
                    f"adjudication {aid}: recorded_at must be strictly after independent "
                    f"pass {ref!r} (labels recorded independently first)"
                )

    # Double-annotated targets: independent passes must be blind.
    for target, passes in independent_by_target.items():
        if len(passes) >= 2:
            for p in passes:
                if not p.get("blind"):
                    errors.append(
                        f"target {target!r}: double-annotated independent pass "
                        f"{p.get('annotation_id')!r} must be blind=true"
                    )
    return _result(errors)


def double_annotation_fraction(family_ids: list[str], records: list[dict]) -> float:
    """Fraction of families with >=2 independent annotation passes (target: >=0.20)."""
    if not family_ids:
        return 0.0
    counts: dict[str, int] = {}
    for r in records:
        if r.get("pass_type") == "independent" and r.get("target_type") == "query_family":
            counts[r.get("target_id")] = counts.get(r.get("target_id"), 0) + 1
    doubled = sum(1 for fid in family_ids if counts.get(fid, 0) >= 2)
    return doubled / len(family_ids)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _num(x: Any) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else float("nan")


def _parse_ts(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_measured(value: Any) -> bool:
    """True iff ``value`` is a real numeric measurement (not the sentinel)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
