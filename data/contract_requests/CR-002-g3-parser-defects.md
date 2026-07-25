# CR-002 — Gate G3 audit findings: five unsafe parses

| Field | Value |
|---|---|
| **Raised by** | Member 4 — Data and Science (audit lane) |
| **Owner of fix** | Member 2 — Query intelligence (`query/parser.py`) |
| **Coordinator** | Member 5 — release owner |
| **Status** | **Open — G3 NOT PASSED** |
| **Raised** | 2026-07-26 |
| **Blocks** | Gate G3; golden case 5 (plan §29); the staged cross-window scenario (§21.2) |

Reproduce: `python -m eval.g3_audit --failures-only`
Machine-readable: [`eval/reports/g3_audit.json`](../../eval/reports/g3_audit.json)

---

## Result

| Metric | Value |
|---|---|
| Queries | 30 (plan minimum 25) ✅ |
| Category coverage | all 16 required categories present ✅ |
| Fully correct | 24/30 |
| **Score** | **80.0%** (threshold 80%) — clears, but only just |
| **Unsafe failures** | **5** (allowed 0) ❌ |
| Survivable failures | 1 |
| **Gate** | **NOT PASSED** |

The score alone would pass. It should not, and the harness does not let it:
five failures are parses where the system reports `interpretation_state=clear`
and would confidently answer **a different question than the one asked**, with
no clarification offered. Plan §13.4 requires failures to be *visible and
survivable*, not merely infrequent. A silently weakened or inverted claim is
not survivable at any score.

The audit downgrades a failure to *survivable* automatically whenever the
parser abstained — asked a clarification or flagged an unsupported construct.
All five below stayed `clear`.

---

## Finding 1 — negated subject parses as a positive claim (most serious)

**`g3_15`: "no one is carrying a bag"** → `interpretation_state=clear`, a normal
`(carries, bag)` relation, **no `visible_none` logic group**.

The query asserts an absence. The plan asserts a presence. This inverts the
user's meaning, and §4 forbids exactly this class of error: an absence that
cannot be certified must never be rendered as a clean claim, and an absence
must never become its opposite.

The `visible_none` path itself works — `g3_14` "no black bag visible in the
lobby" and `g3_20` "no bicycle visible at the entrance" both parse correctly.
The gap is the **negated-subject phrasing** (`no one`, `nobody`) as opposed to
the negated-object phrasing (`no <thing> visible`).

**Requested:** map negated-subject constructions to `visible_none`, or reject
them with a clarification. Either is acceptable; silently inverting is not.

---

## Finding 2 — action lexicon misses gerunds ("placing", "putting")

**`g3_13`: "all occurrences of a person placing a box at the gate"** → atoms are
only `['person', 'gate']`. The action is dropped entirely and the plan is still
`clear`, so the system would retrieve every person near the gate and present it
as an answer about *placing a box*.

Root cause is in `ACTION_PATTERNS` ([query/parser.py:118](../../query/parser.py:118)):

```
("places", re.compile(r"\b(?:place|places|placed|put|puts|left|leaves)\b", re.I))
```

`placing` and `putting` match nothing. `\bput\b` does not match `putting`.

**Requested:** extend the pattern to the `-ing` forms.

---

## Finding 3 — dropped action silently removes a temporal constraint

**`g3_10`: "someone picks up a bag after placing a box"** → `temporal_relations = []`.

This is a consequence of Finding 2, not a separate bug in the temporal code.
`_extract_temporal` ([query/parser.py:377](../../query/parser.py:377)) requires
**two** ACTION atoms; "placing" is not recognised, so only one action survives
and the temporal relation is never built.

The temporal extractor itself is sound — "a person **places** a box before
picking up a bag" (`g3_09`) produces the correct `(places, before, picks_up)`.

What makes this unsafe rather than merely incomplete: the plan still reports
`clear`. §33 item 6 makes temporal assembly *mandatory* for before/after
queries, so a dropped ordering constraint means the system answers the
unordered query and presents it as the ordered one.

---

## Finding 4 — no enter/exit actions at all (blocks golden case 5)

**`g3_11`: "a person entering then later leaving with a bag"** → zero ACTION
atoms, no temporal relation, still `clear`.

There is no `enters`/`leaves` entry in `ACTION_PATTERNS`. (`leaves` appears only
inside the `places` alternation, where it means *left an object behind*, not
*departed* — so "leaving" is either unmatched or mis-typed.)

**This one blocks committed work:**

- §29 golden case 5 is *cross-window order* — the ten-query suite cannot be
  satisfied from a real parse.
- §21.2 requires a staged cross-window event, and our recording shot list
  stages exactly this: an entrance at ~12 min and an exit with the backpack at
  ~22 min of the Gate G1 hour ([data/footage_shotlist.md](../footage_shotlist.md) §2).

We are about to record footage for a query the parser cannot currently express.

**Requested:** add `enters` and `leaves` (departure sense) as first-class
actions, distinct from the `places` alternation.

---

## Finding 5 — superlatives bypass the comparison rejection

**`g3_29`: "the tallest person in the room"** → `clear`, no
`unsupported_constructs`, no clarification.

**`g3_28`: "a person taller than the doorway"** → correctly rejected with
`comparison` and a clarification.

So comparatives are rejected in the *explicit* form and accepted in the
*superlative* form. Both are open-world judgements the system cannot make from
a frame. A rejection that is bypassable by rephrasing is not a rejection.

**Requested:** treat superlatives (`tallest`, `largest`, `closest`, `most …`) as
`comparison`, same as `taller than`.

---

## Not a defect — recorded for completeness

**`g3_22`: "exactly three people in the lobby"** → `clarification_required` with
`unbounded_count_scope`.

The parser asks which continuous interval to count over, because "in the lobby"
is not treated as a scope anchor while "near the door" (`g3_21`) is. This is
**over-asking, not mis-answering** — the safe direction, and §14.3 explicitly
requires counts to have a declared continuous interval. The audit classifies it
as survivable and it does not block the gate.

Member 2 may treat locative `in <place>` as an anchor if desired. Member 4 has
no objection either way and will not encode a preference into the gate.

---

## Acceptance

- [ ] `python -m eval.g3_audit` reports `unsafe failures: 0`
- [ ] Score ≥ 80% maintained
- [ ] `python -m eval.g3_audit --strict` exits 0
- [ ] `eval/reports/g3_audit.json` regenerated and committed
- [ ] Findings 2 and 4 verified against the real shot-list queries before the
      recording day, so we do not stage footage for an unparseable query

## Scope note

Member 4 does not edit `query/`. This report is the deliverable; the fix is
Member 2's. The audit set ([data/queries/g3_audit_set.json](../queries/g3_audit_set.json))
is deliberately declarative so a fix can be verified without reading the harness.
