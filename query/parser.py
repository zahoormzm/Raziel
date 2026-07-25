"""Validated query parser with a deterministic, auditable fallback.

An optional model parser may propose JSON, but every proposal crosses the
``QueryPlanData`` validator.  The model gets at most one repair attempt and
never controls SQL or graph execution.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from query.schema import (
    AtomRole,
    AtomType,
    InterpretationState,
    LogicGroup,
    LogicOperator,
    MAX_ANY_ALTERNATIVES,
    MAX_COUNT_BOUND,
    QueryAtom,
    QueryFilters,
    QueryPlanData,
    QueryRelation,
    QueryValidationError,
    TemporalRelation,
    boundary_mapping,
    coerce_query_plan,
    query_plan_payload,
)


UNIVERSAL_RE = re.compile(r"\b(everyone|everybody|all\s+(?:people|persons|vehicles))\b", re.I)
COMPARISON_RE = re.compile(
    r"\b(larger|smaller|taller|shorter|faster|slower|more\s+than|less\s+than|"
    r"at\s+least\s+as|compared\s+to)\b",
    re.I,
)
EXCEPTION_RE = re.compile(r"\b(except|other\s+than|but\s+not)\b", re.I)
CROSS_CAMERA_ID_RE = re.compile(
    r"\b(same\s+(?:person|vehicle).*(?:another|different)\s+camera|"
    r"across\s+cameras?|cross[- ]camera)\b",
    re.I,
)
NAMED_PERSON_RE = re.compile(
    r"\b(face\s+recognition|identify\s+(?:him|her|them|the\s+person)|"
    r"named\s+person|who\s+is\s+this\s+person)\b",
    re.I,
)
LONG_GAP_ID_RE = re.compile(
    r"\b(same\s+(?:person|vehicle).*(?:minutes?|hours?)\s+later|"
    r"(?:minutes?|hours?)\s+later.*same\s+(?:person|vehicle))\b",
    re.I,
)
ARBITRARY_NEGATION_RE = re.compile(
    r"\b(not|without|neither|nor)\b",
    re.I,
)

COUNT_RE = re.compile(
    r"\b(?:(exactly|at\s+least|at\s+most)\s+)?"
    r"(zero|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(people|persons|person|vehicles|vehicle|cars|car|bags|bag)\b",
    re.I,
)
VISIBLE_NONE_RE = re.compile(
    r"\b(?:no|zero)\s+"
    r"([a-z][a-z0-9 -]{0,48}?)"
    r"\s+(?:is\s+|are\s+)?(?:visible|present|seen)\b",
    re.I,
)
SIMPLE_VISIBLE_NONE_RE = re.compile(
    r"\bno\s+"
    r"((?:(?:black|blue|brown|gray|grey|green|orange|red|white|yellow)\s+)?"
    r"(?:backpacks?|bags?|bicycles?|bikes?|cars?|persons?|people|trucks?|vans?|vehicles?))\b",
    re.I,
)
OR_RE = re.compile(r"\s+(?:or|either)\s+", re.I)
OR_TARGET_RE = re.compile(
    r"\b((?:(?:black|blue|brown|gray|grey|green|orange|red|white|yellow)\s+)?"
    r"(?:backpack|bag|bicycle|bike|car|person|truck|van|vehicle))"
    r"\s+or\s+"
    r"((?:(?:black|blue|brown|gray|grey|green|orange|red|white|yellow)\s+)?"
    r"(?:backpack|bag|bicycle|bike|car|person|truck|van|vehicle))\b",
    re.I,
)
TEMPORAL_RE = re.compile(r"\b(before|after|then|later)\b", re.I)

COLOURS = frozenset(
    {"black", "blue", "brown", "gray", "grey", "green", "orange", "red", "white", "yellow"}
)
OBJECT_WORDS = frozenset(
    {
        "backpack",
        "bag",
        "bicycle",
        "bike",
        "car",
        "door",
        "gate",
        "coat",
        "jacket",
        "person",
        "people",
        "shirt",
        "truck",
        "van",
        "vehicle",
    }
)
LOCATION_WORDS = frozenset(
    {"entrance", "exit", "gate", "door", "zone", "counter", "road", "hallway"}
)
ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("walks away", re.compile(r"\bwalk(?:s|ed|ing)?\s+away\b", re.I)),
    ("picks up", re.compile(r"\bpick(?:s|ed|ing)?\s+up\b", re.I)),
    ("places", re.compile(r"\b(?:place|places|placed|put|puts|left|leaves)\b", re.I)),
    ("enters", re.compile(r"\b(?:enter|enters|entered|arrive|arrives|arrived)\b", re.I)),
    ("exits", re.compile(r"\b(?:exit|exits|exited|leave|leaves|left)\b", re.I)),
    ("carries", re.compile(r"\b(?:carry|carries|carried|carrying)\b", re.I)),
    ("follows", re.compile(r"\b(?:follow|follows|followed|following)\b", re.I)),
)
RELATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("carries", re.compile(r"\b(?:carry|carries|carried|carrying)\b", re.I)),
    ("near", re.compile(r"\b(?:near|beside|next\s+to)\b", re.I)),
    ("wears", re.compile(r"\b(?:wear|wears|wearing|wore)\b", re.I)),
    ("places", re.compile(r"\b(?:place|places|placed|put|puts)\b", re.I)),
    ("picks_up", re.compile(r"\bpick(?:s|ed|ing)?\s+up\b", re.I)),
    ("follows", re.compile(r"\b(?:follow|follows|followed|following)\b", re.I)),
)
NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _clarification(
    text: str, filters: QueryFilters, construct: str, question: str
) -> QueryPlanData:
    return QueryPlanData(
        query_text=text,
        atoms=(),
        filters=filters,
        unsupported_constructs=(construct,),
        clarification_question=question,
        interpretation_state=InterpretationState.CLARIFICATION_REQUIRED,
    )


def _unsupported(text: str, filters: QueryFilters) -> QueryPlanData | None:
    proper_name = re.search(
        r"\b(?i:find|show|track|locate)\s+([A-Z][a-z]{2,})\b", text
    )
    if proper_name and proper_name.group(1).casefold() not in (
        {
            "the",
            "person",
            "people",
            "vehicle",
            "car",
            "truck",
            "van",
        }
        | COLOURS
    ):
        return _clarification(
            text,
            filters,
            "named_person_identification",
            "Can you describe visible clothing, objects, or actions instead of a person's name?",
        )
    checks = (
        (
            UNIVERSAL_RE,
            "universal_quantification",
            "Can you restate this as a bounded visible object/person count in one camera interval?",
        ),
        (
            COMPARISON_RE,
            "comparison",
            "Can you replace the comparison with a directly visible attribute?",
        ),
        (
            EXCEPTION_RE,
            "open_world_exception",
            "Can you state only the visible objects or actions that should be present?",
        ),
        (
            CROSS_CAMERA_ID_RE,
            "cross_camera_identity",
            "Should I search the cameras independently without claiming the same identity?",
        ),
        (
            NAMED_PERSON_RE,
            "person_identification",
            "Can you describe visible clothing, objects, or actions instead of identity?",
        ),
        (
            LONG_GAP_ID_RE,
            "long_gap_identity",
            "Should I search for the ordered events without claiming they involve the same person?",
        ),
        (
            ARBITRARY_NEGATION_RE,
            "open_world_negation",
            "Can you use a bounded 'no X visible' request inside one assessable camera interval?",
        ),
    )
    for pattern, construct, question in checks:
        if pattern.search(text):
            return _clarification(text, filters, construct, question)
    return None


def _normalise_phrase(value: str) -> str:
    value = re.sub(r"^[\s,.;:]+|[\s,.;:]+$", "", value.lower())
    value = re.sub(r"\b(a|an|the|show|find|where|when|of)\b", " ", value)
    value = re.sub(
        r"\b(backpacks|bags|bicycles|bikes|cars|persons|trucks|vans|vehicles)\b",
        lambda match: match.group(1)[:-1],
        value,
    )
    value = re.sub(r"\bpeople\b", "person", value)
    return re.sub(r"\s+", " ", value).strip()


def _atom_type(phrase: str) -> AtomType:
    tokens = set(re.findall(r"[a-z]+", phrase.lower()))
    if tokens & LOCATION_WORDS:
        return AtomType.LOCATION
    if tokens & COLOURS and not tokens & OBJECT_WORDS:
        return AtomType.ATTRIBUTE
    if any(pattern.search(phrase) for _, pattern in ACTION_PATTERNS):
        return AtomType.ACTION
    if tokens & OBJECT_WORDS:
        return AtomType.OBJECT
    return AtomType.SCENE


def _extract_atoms(text: str) -> list[QueryAtom]:
    candidates: list[tuple[str, AtomType, AtomRole]] = []
    lowered = text.lower()

    for colour in sorted(COLOURS):
        pattern = re.compile(
            rf"\b{re.escape(colour)}\s+"
            r"(?:upper\s+clothing|shirt|jacket|coat|bag|backpack|car|vehicle|van|truck)\b",
            re.I,
        )
        match = pattern.search(text)
        if match:
            phrase = match.group(0).lower()
            candidates.append((phrase, AtomType.ATTRIBUTE, AtomRole.CANDIDATE_ANCHOR))
        elif re.search(rf"\b{re.escape(colour)}\b", text, re.I):
            candidates.append(
                (colour, AtomType.ATTRIBUTE, AtomRole.CANDIDATE_ANCHOR)
            )

    action_hits = [
        (match.start(), canonical)
        for canonical, pattern in ACTION_PATTERNS
        if (match := pattern.search(text)) is not None
    ]
    for _position, canonical in sorted(action_hits):
        candidates.append((canonical, AtomType.ACTION, AtomRole.CANDIDATE_ANCHOR))

    tokens = re.findall(r"[a-z]+", lowered)
    for token in tokens:
        if token in OBJECT_WORDS:
            singular = {"people": "person"}.get(token, token)
            candidates.append(
                (
                    singular,
                    (
                        AtomType.LOCATION
                        if singular in LOCATION_WORDS
                        else AtomType.OBJECT
                    ),
                    AtomRole.CANDIDATE_ANCHOR,
                )
            )

    for token in tokens:
        if token in LOCATION_WORDS:
            candidates.append((token, AtomType.LOCATION, AtomRole.CANDIDATE_ANCHOR))

    deduped: list[tuple[str, AtomType, AtomRole]] = []
    seen: set[str] = set()
    for phrase, kind, role in candidates:
        key = phrase.casefold()
        if key and key not in seen:
            seen.add(key)
            deduped.append((phrase, kind, role))

    if not deduped:
        fallback = _normalise_phrase(text)
        if fallback:
            deduped.append((fallback, _atom_type(fallback), AtomRole.CANDIDATE_ANCHOR))
    return [
        QueryAtom(
            atom_id=f"a{index}",
            text_span=phrase,
            type=kind,
            role=role,
        )
        for index, (phrase, kind, role) in enumerate(deduped, start=1)
    ]


def _find_atom(atoms: list[QueryAtom], words: set[str], *, reverse: bool = False) -> str | None:
    sequence = reversed(atoms) if reverse else iter(atoms)
    for atom in sequence:
        if set(re.findall(r"[a-z]+", atom.text_span.lower())) & words:
            return atom.atom_id
    return None


def _extract_relations(text: str, atoms: list[QueryAtom]) -> list[QueryRelation]:
    if len(atoms) < 2:
        return []
    relations: list[QueryRelation] = []
    person_id = _find_atom(atoms, {"person", "people"})
    object_id = _find_atom(
        atoms,
        {
            "bag",
            "backpack",
            "vehicle",
            "car",
            "van",
            "truck",
            "shirt",
            "jacket",
            "coat",
        },
        reverse=True,
    )
    location_id = _find_atom(
        atoms, {"gate", "door", "entrance", "exit", "zone"}, reverse=True
    )
    for predicate, pattern in RELATION_PATTERNS:
        if not pattern.search(text):
            continue
        subject = person_id or atoms[0].atom_id
        if predicate == "near":
            obj = location_id or object_id
        elif predicate == "follows":
            people = [a.atom_id for a in atoms if "person" in a.text_span]
            obj = people[1] if len(people) > 1 else object_id
        else:
            obj = object_id or location_id
        if obj and obj != subject:
            relations.append(
                QueryRelation(
                    relation_id=f"r{len(relations) + 1}",
                    subject_atom=subject,
                    predicate=predicate,
                    object_atom=obj,
                )
            )
    return relations


def _extract_temporal(text: str, atoms: list[QueryAtom]) -> list[TemporalRelation]:
    match = TEMPORAL_RE.search(text)
    actions = [atom for atom in atoms if atom.type is AtomType.ACTION]
    if not match or len(actions) < 2:
        return []
    marker = match.group(1).lower()
    first, second = actions[0], actions[1]
    relation = "after" if marker == "after" else "before"
    if relation == "after":
        first, second = second, first
        relation = "before"
    return [
        TemporalRelation(
            first_atom=first.atom_id,
            relation=relation,
            second_atom=second.atom_id,
            max_gap_s=(
                30.0
                if re.search(r"\bsame\s+(?:person|vehicle)\b", text, re.I)
                else 600.0
            ),
            same_actor_required=bool(
                re.search(r"\bsame\s+(?:person|vehicle)\b", text, re.I)
            ),
        )
    ]


def _parse_logic(
    text: str, atoms: list[QueryAtom], filters: QueryFilters
) -> tuple[list[QueryAtom], list[LogicGroup], QueryPlanData | None]:
    visible_none = VISIBLE_NONE_RE.search(text) or SIMPLE_VISIBLE_NONE_RE.search(text)
    if visible_none:
        target = _normalise_phrase(visible_none.group(1))
        if not target:
            return atoms, [], _clarification(
                text,
                filters,
                "ambiguous_visible_none",
                "Which visible object should be absent in the declared interval?",
            )
        existing_index = next(
            (
                index
                for index, atom in enumerate(atoms)
                if atom.text_span.casefold() == target.casefold()
            ),
            None,
        )
        target_atom = QueryAtom(
            atom_id=(
                atoms[existing_index].atom_id
                if existing_index is not None
                else f"a{len(atoms) + 1}"
            ),
            text_span=target,
            type=(
                atoms[existing_index].type
                if existing_index is not None
                else _atom_type(target)
            ),
            role=AtomRole.VERIFIER_ONLY,
        )
        if existing_index is None:
            atoms.append(target_atom)
        else:
            atoms[existing_index] = target_atom
        other_anchors = [
            atom
            for atom in atoms
            if atom.atom_id != target_atom.atom_id
            and atom.role is AtomRole.CANDIDATE_ANCHOR
        ]
        explicit_scope = (
            filters.start_time is not None
            and filters.end_time is not None
            and len(filters.camera_ids) <= 1
            and len(filters.video_ids) <= 1
            and bool(filters.camera_ids or filters.video_ids)
        )
        if (
            len(filters.camera_ids) > 1
            or len(filters.video_ids) > 1
            or (not explicit_scope and not other_anchors)
        ):
            return atoms, [], _clarification(
                text,
                filters,
                "unbounded_visible_absence",
                "Which single camera/video interval or assessable candidate episode should be checked for visible absence?",
            )
        return (
            atoms,
            [
                LogicGroup(
                    group_id="g1",
                    operator=LogicOperator.VISIBLE_NONE,
                    atom_ids=(target_atom.atom_id,),
                    observation_scope="camera_time_interval"
                    if filters.start_time is not None and filters.end_time is not None
                    else "candidate_episode",
                )
            ],
            None,
        )

    count_match = COUNT_RE.search(text)
    if count_match:
        qualifier, number_raw, noun = count_match.groups()
        number = NUMBER_WORDS.get(number_raw.lower(), int(number_raw) if number_raw.isdigit() else -1)
        if number > MAX_COUNT_BOUND:
            return atoms, [], _clarification(
                text,
                filters,
                "unbounded_count",
                f"Can you use a count no greater than {MAX_COUNT_BOUND} in one continuous camera interval?",
            )
        noun = {"people": "person", "persons": "person", "vehicles": "vehicle", "cars": "car", "bags": "bag"}.get(noun.lower(), noun.lower())
        atom_id = _find_atom(atoms, {noun})
        if atom_id is None:
            atom_id = f"a{len(atoms) + 1}"
            atoms.append(
                QueryAtom(
                    atom_id=atom_id,
                    text_span=noun,
                    type=AtomType.OBJECT,
                    role=AtomRole.CANDIDATE_ANCHOR,
                )
            )
        other_anchors = [
            atom
            for atom in atoms
            if atom.atom_id != atom_id and atom.role is AtomRole.CANDIDATE_ANCHOR
        ]
        explicit_scope = (
            filters.start_time is not None
            and filters.end_time is not None
            and len(filters.camera_ids) <= 1
            and len(filters.video_ids) <= 1
            and bool(filters.camera_ids or filters.video_ids)
        )
        if (
            len(filters.camera_ids) > 1
            or len(filters.video_ids) > 1
            or (not explicit_scope and not other_anchors)
        ):
            return atoms, [], _clarification(
                text,
                filters,
                "unbounded_count_scope",
                "Which single continuous camera/video interval should be used for the bounded count?",
            )
        min_count: int | None
        max_count: int | None
        qualifier_norm = (qualifier or "exactly").lower()
        if qualifier_norm == "at least":
            min_count, max_count = number, MAX_COUNT_BOUND
        elif qualifier_norm == "at most":
            min_count, max_count = 0, number
        else:
            min_count = max_count = number
        return (
            atoms,
            [
                LogicGroup(
                    group_id="g1",
                    operator=LogicOperator.COUNT,
                    atom_ids=(atom_id,),
                    observation_scope="camera_time_interval"
                    if filters.start_time is not None and filters.end_time is not None
                    else "candidate_episode",
                    min_count=min_count,
                    max_count=max_count,
                )
            ],
            None,
        )

    if re.search(r"\bor\b", text, re.I):
        target_match = (
            OR_TARGET_RE.search(text)
            if len(re.findall(r"\bor\b", text, re.I)) == 1
            else None
        )
        parts = (
            [target_match.group(1), target_match.group(2)]
            if target_match
            else [part for part in OR_RE.split(text) if _normalise_phrase(part)]
        )
        if len(parts) > MAX_ANY_ALTERNATIVES:
            return atoms, [], _clarification(
                text,
                filters,
                "unbounded_disjunction",
                f"Can you limit the visible alternatives to at most {MAX_ANY_ALTERNATIVES}?",
            )
        alternative_ids: list[str] = []
        for part in parts:
            phrase = _normalise_phrase(part)
            existing = next(
                (
                    atom.atom_id
                    for atom in atoms
                    if atom.text_span.casefold() == phrase.casefold()
                ),
                None,
            )
            if existing is None:
                existing = f"a{len(atoms) + 1}"
                atoms.append(
                    QueryAtom(
                        atom_id=existing,
                        text_span=phrase,
                        type=_atom_type(phrase),
                    )
                )
            alternative_ids.append(existing)
        if len(alternative_ids) >= 2:
            alternative_phrases = {
                atom.text_span.casefold()
                for atom in atoms
                if atom.atom_id in alternative_ids
            }
            # Remove redundant generic atoms wholly contained in a more
            # specific alternative ("car" beside "red car").  Shared context
            # atoms, actions, and relations remain conjunctive.
            atoms = [
                atom
                for atom in atoms
                if atom.atom_id in alternative_ids
                or atom.type is AtomType.ACTION
                or not any(
                    set(atom.text_span.casefold().split()).issubset(
                        phrase.split()
                    )
                    for phrase in alternative_phrases
                )
            ]
            return (
                atoms,
                [
                    LogicGroup(
                        group_id="g1",
                        operator=LogicOperator.ANY,
                        atom_ids=tuple(alternative_ids),
                    )
                ],
                None,
            )
    return atoms, [], None


def deterministic_parse(
    text: str,
    filters: QueryFilters | Mapping[str, Any] | None = None,
    *,
    fallback_state: bool = False,
) -> QueryPlanData:
    """Parse the supported subset without model or network dependencies."""

    if not text or not text.strip():
        raise QueryValidationError("query text cannot be empty")
    if isinstance(filters, QueryFilters):
        query_filters = filters
    else:
        query_filters = coerce_query_plan(
            {
                "query_text": "_",
                "atoms": [
                    {
                        "atom_id": "a1",
                        "text_span": "_",
                        "type": "scene",
                    }
                ],
                "filters": dict(filters or {}),
            }
        ).filters

    unsupported = _unsupported(text, query_filters)
    if unsupported:
        return unsupported

    atoms = _extract_atoms(text)
    atoms, logic, logic_error = _parse_logic(text, atoms, query_filters)
    if logic_error:
        return logic_error
    relations = _extract_relations(text, atoms)
    temporal = _extract_temporal(text, atoms)
    plan = QueryPlanData(
        query_text=text.strip(),
        atoms=tuple(atoms),
        relations=tuple(relations),
        temporal_relations=tuple(temporal),
        logic_groups=tuple(logic),
        filters=query_filters,
        ambiguities=(
            ("maybe_language_requires_confirmation",)
            if re.search(r"\b(maybe|perhaps|possibly)\b", text, re.I)
            else ()
        ),
        interpretation_state=(
            InterpretationState.PARSER_FALLBACK
            if fallback_state
            else InterpretationState.CLEAR
        ),
        parser_version=(
            "deterministic-fallback-v1"
            if fallback_state
            else "deterministic-v1"
        ),
    )
    return coerce_query_plan(query_plan_payload(plan))


def _invoke_model_parser(
    parser: Callable[..., Any],
    text: str,
    filters: QueryFilters | Mapping[str, Any] | None,
    validation_error: str | None,
) -> Any:
    parameters = inspect.signature(parser).parameters
    kwargs: dict[str, Any] = {}
    if "filters" in parameters:
        kwargs["filters"] = filters
    if "validation_error" in parameters:
        kwargs["validation_error"] = validation_error
    return parser(text, **kwargs)


def parse_query(
    text: str,
    filters: QueryFilters | Mapping[str, Any] | None = None,
    *,
    model_parser: Callable[..., Any] | None = None,
) -> QueryPlanData:
    """Parse with schema validation, one repair, then deterministic fallback."""

    if model_parser is None:
        return deterministic_parse(text, filters)
    validation_error: str | None = None
    for _attempt in range(2):
        try:
            proposal = _invoke_model_parser(model_parser, text, filters, validation_error)
            if isinstance(proposal, str):
                proposal = json.loads(proposal)
            proposal_map = boundary_mapping(proposal)
            proposal_map.setdefault("query_text", text)
            proposal_map.setdefault("parser_version", "model-parser-v1")
            proposal_map.setdefault(
                "filters",
                query_plan_payload(deterministic_parse("_", filters))["filters"],
            )
            return coerce_query_plan(proposal_map, query_text=text)
        except (TypeError, ValueError, json.JSONDecodeError, QueryValidationError) as exc:
            validation_error = str(exc)
    return deterministic_parse(text, filters, fallback_state=True)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Parse a RAZIEL bounded query")
    cli.add_argument("text")
    cli.add_argument("--camera", action="append", default=[])
    cli.add_argument("--video", action="append", default=[])
    cli.add_argument("--start", type=float)
    cli.add_argument("--end", type=float)
    args = cli.parse_args(argv)
    plan = deterministic_parse(
        args.text,
        {
            "camera_ids": args.camera,
            "video_ids": args.video,
            "start_time": args.start,
            "end_time": args.end,
        },
    )
    print(json.dumps(query_plan_payload(plan), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
