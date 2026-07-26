"""Gate G3 parser correctness audit (plan 13.4).

Runs the deterministic parser over the scripted audit set in
``data/queries/g3_audit_set.json`` and scores each parse against declarative
assertions. The gate requires at least 25 queries covering every listed
category, at least 80% fully correct, and **every failure visible and
survivable** -- so a failing parse is reported with the exact assertion that
broke, never swallowed.

Scope note: the parser lives in ``query/`` (Member 2). This module audits it and
never modifies it. Findings are reported for the owning lane to act on.

Run::

    python -m eval.g3_audit                 # human-readable report
    python -m eval.g3_audit --json          # machine-readable
    python -m eval.g3_audit --failures-only # just what broke
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AUDIT_SET_PATH = REPO_ROOT / "data" / "queries" / "g3_audit_set.json"

# Categories the plan (13.4) requires the audit set to cover.
REQUIRED_CATEGORIES = {
    "object", "attribute", "action", "binding", "temporal", "multi_occurrence",
    "absent", "ambiguous", "bounded_or", "visible_absence", "small_count",
    "unbounded_count_rejected", "unsupported_negation", "unsupported_universal",
    "unsupported_comparative", "long_gap_identity",
}


def load_audit_set(path: Path = AUDIT_SET_PATH) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing audit set: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Plan introspection helpers
# --------------------------------------------------------------------------- #

def _enum_value(value: Any) -> Any:
    """Enum members render as their value; everything else passes through."""
    return getattr(value, "value", value)


def plan_to_dict(plan: Any) -> dict:
    raw = dataclasses.asdict(plan)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        return _enum_value(node)

    return walk(raw)


def _spans(plan: dict) -> list[str]:
    return [a.get("text_span", "") for a in plan.get("atoms", [])]


def _atom_span(plan: dict, atom_id: str) -> str:
    for atom in plan.get("atoms", []):
        if atom.get("atom_id") == atom_id:
            return atom.get("text_span", "")
    return ""


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", value.strip().lower()) if token]


def _span_matches(actual: str, expected: str) -> bool:
    """Whole-token containment. Tolerates 'black backpack' vs 'backpack'.

    Not a substring test. `expected in actual` accepted 'car' for an atom span
    of 'carries', 'van' for 'advantage' and 'son' for 'person' -- the same defect
    class that was fixed in evidence/predicates.py, sitting inside the harness
    that certifies Gate G3. It also let a forbid_atom_spans guard fire on an
    innocent substring. No assertion in the current 31-case set depended on the
    loose behaviour, so this tightens the audit without changing its verdict.
    """
    actual_tokens = set(_tokens(actual))
    expected_tokens = _tokens(expected)
    return bool(expected_tokens) and all(
        token in actual_tokens for token in expected_tokens
    )


def _any_span_matches(plan: dict, expected: str) -> bool:
    return any(_span_matches(span, expected) for span in _spans(plan))


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #

def check_query(case: dict, plan: dict) -> list[str]:
    """Return a list of assertion failures; empty means fully correct."""
    expect = case.get("expect", {})
    failures: list[str] = []

    if "interpretation_state" in expect:
        want = expect["interpretation_state"]
        got = plan.get("interpretation_state")
        if got != want:
            failures.append(f"interpretation_state: expected {want!r}, got {got!r}")

    if expect.get("clarification_required"):
        state = plan.get("interpretation_state")
        if state != "clarification_required":
            failures.append(
                f"expected clarification_required, got interpretation_state={state!r}"
            )
        if not plan.get("clarification_question"):
            failures.append("expected a clarification_question, got none")

    for construct in expect.get("unsupported_include", []):
        if construct not in list(plan.get("unsupported_constructs", [])):
            failures.append(
                f"unsupported_constructs missing {construct!r} "
                f"(got {list(plan.get('unsupported_constructs', []))})"
            )

    for ambiguity in expect.get("ambiguities_include", []):
        if ambiguity not in list(plan.get("ambiguities", [])):
            failures.append(
                f"ambiguities missing {ambiguity!r} (got {list(plan.get('ambiguities', []))})"
            )

    groups = plan.get("logic_groups", [])
    if "logic_operator" in expect:
        want = expect["logic_operator"]
        operators = [g.get("operator") for g in groups]
        if want not in operators:
            failures.append(f"logic_groups missing operator {want!r} (got {operators})")
        else:
            group = next(g for g in groups if g.get("operator") == want)
            if "logic_min_count" in expect and group.get("min_count") != expect["logic_min_count"]:
                failures.append(
                    f"logic min_count: expected {expect['logic_min_count']}, "
                    f"got {group.get('min_count')}"
                )
            if "logic_max_count" in expect and group.get("max_count") != expect["logic_max_count"]:
                failures.append(
                    f"logic max_count: expected {expect['logic_max_count']}, "
                    f"got {group.get('max_count')}"
                )
            if "logic_atom_count" in expect:
                actual = len(group.get("atom_ids", []))
                if actual != expect["logic_atom_count"]:
                    failures.append(
                        f"logic group atom count: expected "
                        f"{expect['logic_atom_count']}, got {actual}"
                    )

    for span in expect.get("atom_spans_include", []):
        if not _any_span_matches(plan, span):
            failures.append(f"no atom span matching {span!r} (spans={_spans(plan)})")

    for atom_type in expect.get("atom_types_include", []):
        if atom_type not in [a.get("type") for a in plan.get("atoms", [])]:
            failures.append(f"no atom of type {atom_type!r}")

    # The parser must not invent attributes it was never given (plan 13.2).
    for span in expect.get("forbid_atom_spans", []):
        if _any_span_matches(plan, span):
            failures.append(f"invented/forbidden atom span {span!r} present")

    for subject, predicate, obj in expect.get("relations_include", []):
        found = any(
            _span_matches(_atom_span(plan, r.get("subject_atom", "")), subject)
            and r.get("predicate") == predicate
            and _span_matches(_atom_span(plan, r.get("object_atom", "")), obj)
            for r in plan.get("relations", [])
        )
        if not found:
            actual = [
                (
                    _atom_span(plan, r.get("subject_atom", "")),
                    r.get("predicate"),
                    _atom_span(plan, r.get("object_atom", "")),
                )
                for r in plan.get("relations", [])
            ]
            failures.append(
                f"missing relation ({subject}, {predicate}, {obj}); got {actual}"
            )

    for first, relation, second in expect.get("temporal_include", []):
        found = any(
            _span_matches(_atom_span(plan, t.get("first_atom", "")), first)
            and t.get("relation") == relation
            and _span_matches(_atom_span(plan, t.get("second_atom", "")), second)
            for t in plan.get("temporal_relations", [])
        )
        if not found:
            actual = [
                (
                    _atom_span(plan, t.get("first_atom", "")),
                    t.get("relation"),
                    _atom_span(plan, t.get("second_atom", "")),
                )
                for t in plan.get("temporal_relations", [])
            ]
            failures.append(
                f"missing temporal ({first}, {relation}, {second}); got {actual}"
            )

    if "temporal_same_actor" in expect:
        want = expect["temporal_same_actor"]
        actual = [t.get("same_actor_required") for t in plan.get("temporal_relations", [])]
        if want not in actual:
            failures.append(f"temporal same_actor_required: expected {want}, got {actual}")

    if "min_atoms" in expect and len(plan.get("atoms", [])) < expect["min_atoms"]:
        failures.append(
            f"expected at least {expect['min_atoms']} atoms, got {len(plan.get('atoms', []))}"
        )

    # Plan 13.2: "The parser cannot invent attributes." The rule targets
    # ATTRIBUTES specifically. Action and object atoms are legitimately
    # canonicalised ("carrying" -> "carries", "people" -> "person"), and
    # flagging those as inventions would be a harness bug, not a parser defect.
    query_words = set(case["text"].lower().replace(",", " ").split())
    for atom in plan.get("atoms", []):
        if atom.get("type") != "attribute":
            continue
        span = atom.get("text_span", "")
        if span and not any(word in query_words for word in span.lower().split()):
            failures.append(
                f"invented attribute {span!r}: no word of it appears in the query"
            )

    return failures


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #

def run_audit(audit_set: dict | None = None) -> dict:
    from query.parser import deterministic_parse  # imported late: Member 2's lane

    audit_set = audit_set or load_audit_set()
    gate = audit_set.get("gate", {})
    threshold = float(gate.get("pass_threshold", 0.80))
    minimum = int(gate.get("minimum_queries", 25))

    results = []
    for case in audit_set["queries"]:
        try:
            # A case may declare filters so the scoped and unscoped forms of the
            # same sentence can be audited separately -- absence and count claims
            # are legitimately different with and without a declared interval.
            filters = case.get("filters")
            plan = plan_to_dict(
                deterministic_parse(case["text"], filters)
                if filters
                else deterministic_parse(case["text"])
            )
            failures = check_query(case, plan)
            error = None
        except Exception as exc:  # a crash is a failure, never a skipped case
            plan, error = {}, f"{type(exc).__name__}: {exc}"
            failures = [f"parser raised {error}"]

        # Effective severity is what the parser ACTUALLY did, not just the static
        # label. If the plan abstained -- asked a clarification, or flagged an
        # unsupported construct -- the user was told the system was unsure, so the
        # failure is survivable whatever the query's declared severity. Only a
        # confidently-CLEAR wrong parse is unsafe.
        abstained = bool(
            plan.get("interpretation_state") == "clarification_required"
            or plan.get("unsupported_constructs")
            or plan.get("clarification_question")
        )
        declared = case.get("failure_severity", "unsafe")
        severity = "safe_abstain" if abstained else declared

        results.append({
            "id": case["id"],
            "category": case["category"],
            "text": case["text"],
            "fully_correct": not failures,
            "failures": failures,
            "severity": severity if failures else None,
            "declared_severity": declared,
            "parser_abstained": abstained,
            "notes": case.get("notes"),
            "parser_error": error,
            "interpretation_state": plan.get("interpretation_state"),
        })

    total = len(results)
    correct = sum(1 for r in results if r["fully_correct"])
    score = correct / total if total else 0.0
    unsafe_failures = [r for r in results if r["severity"] == "unsafe"]
    max_unsafe = int(gate.get("max_unsafe_failures", 0))

    by_category: dict[str, dict] = {}
    for result in results:
        bucket = by_category.setdefault(result["category"], {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += int(result["fully_correct"])
    for bucket in by_category.values():
        bucket["score"] = bucket["correct"] / bucket["total"]

    missing_categories = sorted(REQUIRED_CATEGORIES - set(by_category))

    return {
        "audit_schema_version": "1.0.0",
        "audit_set_version": audit_set.get("audit_set_version"),
        "plan_section": gate.get("plan_section", "13.4"),
        "total_queries": total,
        "minimum_queries": minimum,
        "fully_correct": correct,
        "score": round(score, 4),
        "pass_threshold": threshold,
        "meets_query_minimum": total >= minimum,
        "missing_required_categories": missing_categories,
        "unsafe_failures": len(unsafe_failures),
        "safe_abstain_failures": sum(
            1 for r in results if r["severity"] == "safe_abstain"
        ),
        "max_unsafe_failures": max_unsafe,
        # Plan 13.4 requires failures be visible AND survivable. A score above
        # threshold does not carry the gate while an unsafe failure stands: an
        # inverted or silently weakened claim is not survivable at any score.
        "gate_passed": (
            total >= minimum
            and score >= threshold
            and not missing_categories
            and len(unsafe_failures) <= max_unsafe
        ),
        "by_category": by_category,
        "results": results,
    }


def format_report(report: dict, failures_only: bool = False) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 72)
    add("Gate G3 - parser correctness audit (plan section "
        f"{report['plan_section']})")
    add("=" * 72)
    add("")
    add(f"queries          : {report['total_queries']} "
        f"(minimum {report['minimum_queries']}: "
        f"{'OK' if report['meets_query_minimum'] else 'NOT MET'})")
    add(f"fully correct    : {report['fully_correct']}/{report['total_queries']}")
    add(f"score            : {report['score']:.1%} "
        f"(threshold {report['pass_threshold']:.0%})")
    add(f"unsafe failures  : {report['unsafe_failures']} "
        f"(allowed {report['max_unsafe_failures']})")
    add(f"safe abstentions : {report['safe_abstain_failures']}")
    if report["missing_required_categories"]:
        add(f"missing categories: {', '.join(report['missing_required_categories'])}")
    add(f"GATE             : {'PASSED' if report['gate_passed'] else 'NOT PASSED'}")
    if report["score"] >= report["pass_threshold"] and report["unsafe_failures"]:
        add("")
        add(f"  NOTE: the {report['score']:.0%} score clears the threshold, but "
            f"{report['unsafe_failures']} unsafe failure(s) block the gate.")
        add("  An unsafe parse lets the system answer a different question than the")
        add("  one asked, with no clarification. Plan 13.4 requires failures to be")
        add("  survivable, not merely infrequent.")
    add("")

    add("-" * 72)
    add("By category")
    add("-" * 72)
    for name in sorted(report["by_category"]):
        bucket = report["by_category"][name]
        mark = "ok  " if bucket["correct"] == bucket["total"] else "FAIL"
        add(f"  {mark} {name:<28} {bucket['correct']}/{bucket['total']}")
    add("")

    failing = [r for r in report["results"] if not r["fully_correct"]]
    if failing:
        # Unsafe first: those are the ones that block the gate.
        for severity, heading in (
            ("unsafe", "UNSAFE failures - a wrong question answered confidently"),
            ("safe_abstain", "Survivable failures - under-extracts or over-asks, never asserts"),
        ):
            bucket = [r for r in failing if r["severity"] == severity]
            if not bucket:
                continue
            add("-" * 72)
            add(f"{heading} ({len(bucket)})")
            add("-" * 72)
            for result in bucket:
                add("")
                add(f"  [{result['id']}] {result['category']}")
                add(f"  query   : {result['text']}")
                add(f"  state   : {result['interpretation_state']}")
                for failure in result["failures"]:
                    add(f"  FAIL    : {failure}")
                if result["notes"]:
                    add(f"  why it matters: {result['notes']}")
            add("")

    if not failures_only:
        passing = [r for r in report["results"] if r["fully_correct"]]
        add("-" * 72)
        add(f"Passing ({len(passing)})")
        add("-" * 72)
        for result in passing:
            add(f"  ok   [{result['id']}] {result['category']:<26} {result['text']}")
        add("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate G3 parser correctness audit.")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    parser.add_argument("--failures-only", action="store_true", help="omit the passing list")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON report here")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit non-zero unless the gate passes (for CI once G3 is expected green)",
    )
    args = parser.parse_args(argv)

    report = run_audit()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report, failures_only=args.failures_only))

    if args.out:
        out = args.out if args.out.is_absolute() else (Path.cwd() / args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not args.json:
            try:
                shown = out.resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                shown = str(out)
            print(f"Wrote {shown}")

    return 0 if (report["gate_passed"] or not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
