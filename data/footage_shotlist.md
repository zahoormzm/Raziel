# RAZIEL staged footage — recording shot list

**Owner:** Member 4 — Data and Science. **Executed by:** Person 5 (data steward).
**Authority:** `RAZIEL_Master_Execution_Plan_v1.3.md` §21.2 (required staged scenarios),
§21.4 (challenger types), §21.5 (split discipline), §11.4 (Gate G1).
**Companion:** [`footage_collection_protocol.md`](footage_collection_protocol.md) (consent,
manifests, roles), [`ledger.md`](ledger.md) (what happens after recording).

Recording day is a checklist, not an improvisation. Every scene below exists to produce a
specific labelled capability or a specific typed challenger. **If a scene is skipped, name
the families it kills** — do not quietly drop it.

---

## 0. Session plan at a glance

Total staged footage: **~2 h 55 min across 11 sessions**. Estimated yield: **64 families**
(target 60, hard minimum 40).

| # | `session_id` | `scenario_id` | Split | Cam | Duration | Yield | Purpose |
|---|---|---|---|---|---|---|---|
| 1 | `sess_001` | `scn_hour_main` | **test** | cam_01 | **60 min** | 12 | G1 anchor; repeated event; cross-window; empty intervals |
| 2 | `sess_002` | `scn_hour_dev` | **dev** | cam_01 | 20 min | 6 | Cross-window + empty interval, tunable |
| 3 | `sess_003` | `scn_bag_colour` | **train** | cam_01 | 12 min | 6 | Black-bag positive; blue-bag decoy; similar actor |
| 4 | `sess_004` | `scn_binding` | **train** | cam_01 | 10 min | 6 | Correct + wrong person/object binding |
| 5 | `sess_005` | `scn_order` | **train** | cam_01 | 10 min | 6 | Correct order; reversed order; partial event |
| 6 | `sess_006` | `scn_counts` | **test** | cam_02 | 15 min | 6 | Bounded counts; occlusion; fragmentation + duplicate track |
| 7 | `sess_007` | `scn_absence` | **dev** | cam_02 | 12 min | 6 | Visible absence, clear **and** occluded |
| 8 | `sess_008` | `scn_dark` | **test** | cam_02 | 10 min | 4 | Dark / heavily occluded → unobservable |
| 9 | `sess_009` | `scn_disjunction` | **train** | cam_01 | 8 min | 4 | Bounded OR alternatives |
| 10 | `sess_010` | `scn_abandonment` | **train** | cam_01 | 8 min | 4 | Short interruption vs. true abandonment |
| 11 | `sess_011` | `scn_no_event` | **test** | cam_03 | 10 min | 4 | Genuinely empty search interval |

**Split totals: train 26 / dev 12 / test 26.** Assign these **now**, before anyone sees a
system result. A `scenario_id` never appears under two splits.

Organizer footage, if it arrives, goes to `sess_101+` / `scn_org_*` in the **organizer pool**
and is split separately. It never shares a scenario with staged footage.

---

## 1. Setup — do this before recording anything

### Cameras

Three fixed positions. They do not move, pan, or zoom for the entire day.

| Cam | Placement | Used by |
|---|---|---|
| `cam_01` | Wide view of a corridor or lobby **including a doorway/entrance** | S1–S5, S9, S10 |
| `cam_02` | Wide view of an open area where 3 people fit with room to cross paths; one pillar, column, or large object to occlude behind | S6–S8 |
| `cam_03` | A quiet area with low traffic | S11 |

Lock exposure and white balance. Constant frame rate. Mount, do not hand-hold.

### Props

- **Black backpack** (the canonical positive object)
- **Blue backpack** — same shape/size as the black one. The *only* difference is colour.
- **Black suitcase** or holdall — black, but not a backpack (wrong-object decoy)
- **Cap** and **wide-brim hat** (bounded-OR alternatives)
- **Red jacket** and **orange jacket** (second bounded-OR pair)
- A cardboard box or crate
- Two visually similar outfits for two different people (similar-actor decoy)

> The blue bag must genuinely match the black bag in every respect but colour. If it is also
> a different shape, the wrong-attribute challenger tests shape instead of colour and the
> family is worthless.

### Participants

Six people is comfortable; four is workable. Anonymous ids `P1`–`P6` are assigned **per
camera/session** at ledger time — a participant is not "the same P2" across sessions, and
nothing in the dataset asserts identity.

### Slate every session

Before each session, hold a slate (paper or phone screen) in frame for 3 seconds showing the
`session_id`. This is how Person 1 confirms the file matches the manifest without relying on
filenames.

---

## 2. Session 1 — `sess_001` · the G1 hour · **60 min uninterrupted** · cam_01

**This is the single most important file of the day. Record it first.**

Start the recording, then leave it running for the full hour. Everything below happens
*inside* one continuous take. Use a stopwatch started at recording start and read the
minute-marks off it — those become approximate PTS anchors for Person 1.

| Approx. minute | Scene |
|---|---|
| 00–04 | **Empty interval A.** Nobody enters frame at all. Genuinely nothing. |
| 04–06 | **Repeated event, occurrence 1.** P1 walks to the doorway carrying the **black backpack**, sets it down, picks it up, leaves. |
| 06–10 | Ambient traffic — people walking through, no target event. |
| 10–12 | **Repeated event, occurrence 2.** Same action, different person (P2), same black backpack. |
| 12–14 | **Cross-window part A: entrance.** P3 enters through the doorway carrying **nothing**. Clearly empty-handed, clearly visible. |
| 14–20 | Ambient traffic. P3 is out of frame. |
| 20–22 | **Repeated event, occurrence 3.** Same action, P1 again. |
| 22–24 | **Cross-window part B: exit with bag.** P3 exits through the doorway **carrying the black backpack** — ~10 minutes after the entrance. |
| 24–30 | **Empty interval B.** Nobody in frame. |
| 30–33 | **Repeated event, occurrence 4.** Same action, P2. |
| 33–40 | Ambient traffic; someone carries the **blue** backpack through (in-context decoy). |
| 40–44 | **Empty interval C.** Nobody in frame. |
| 44–48 | Two people converse near the doorway, no bag involved. |
| 48–52 | P4 walks through wearing a **cap**. |
| 52–56 | Ambient traffic. |
| 56–60 | **Empty interval D.** Nobody in frame. Let the recording run out. |

**Produces:** the G1 hour; a repeated event with **4** occurrences (≥3 required); the
cross-window entrance→exit-with-bag pair with a ~10-minute gap; four genuinely empty
intervals; `repeated_events` and in-context `wrong_attribute` challengers.

**Verify before striking the camera:**

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 artifacts/authorized_footage/staged/session_001/cam_01.mp4
```

If this prints anything below `3600`, the camera split the file. **Re-record.** Do not
proceed with a split hour — G1 requires no timestamp regression across one continuous file.

---

## 3. Session 2 — `sess_002` · 20 min · cam_01 · **dev**

The development counterpart to S1, so thresholds can be tuned on long-scope behaviour without
touching the test split.

| Approx. minute | Scene |
|---|---|
| 00–03 | Empty interval. |
| 03–05 | P1 enters carrying nothing. |
| 05–12 | Ambient traffic. |
| 12–14 | P1 exits carrying the **black suitcase** (not the backpack — different object, same cross-window shape). |
| 14–17 | Empty interval. |
| 17–20 | Two people walk through, no bag. |

**Produces:** dev-split cross-window order, dev-split empty-set family, dev-split
`wrong_object` challenger.

---

## 4. Session 3 — `sess_003` · 12 min · cam_01 · **train**

The colour-attribute core.

1. **(0–2 min) Black-bag positive.** P1 walks in with the **black backpack**, clearly visible,
   good lighting, unoccluded. Pauses 5 seconds facing the camera so the colour is unambiguous.
2. **(2–4 min) Blue-bag wrong-attribute decoy.** P2 performs the **identical** action with the
   **blue** backpack. Same path, same pause, same framing.
3. **(4–6 min) Wrong-object decoy.** P1 performs the identical action with the **black
   suitcase**. Black, but not a backpack.
4. **(6–9 min) Visually similar actor.** P3 and P4 wear the two similar outfits. P3 carries the
   black backpack; P4 carries nothing and walks the same path 30 seconds later.
5. **(9–12 min) Clean repeat of the positive** from a slightly different path, for boundary
   labelling practice.

**Produces:** `wrong_attribute`, `wrong_object`, `visually_similar_actor` challengers; the
canonical black-bag positive.

---

## 5. Session 4 — `sess_004` · 10 min · cam_01 · **train**

Binding: *who* is doing *what with which object*.

1. **(0–2 min) Correct binding.** P1 wearing the **red jacket** carries the **black backpack**.
   P2 wearing the **orange jacket** carries **nothing**. Both in frame together.
2. **(2–4 min) Wrong-person binding.** P2 (orange jacket) carries the black backpack; P1 (red
   jacket) carries nothing. Same framing — only the binding swapped.
3. **(4–6 min) Wrong-object binding.** P1 (red jacket) carries the **blue** backpack while P2
   carries the black one.
4. **(6–8 min) Three-way.** P1, P2, P3 in frame; only P3 carries the black backpack.
5. **(8–10 min) Handoff.** P1 carries the black backpack, hands it to P2, P2 walks off with it.
   Both are "a person carrying a black backpack" — at different times.

**Produces:** `wrong_binding` challengers (person and object variants). Scene 5 is the hard
case: a correct answer must resolve *when*, not just *whether*.

---

## 6. Session 5 — `sess_005` · 10 min · cam_01 · **train**

Temporal order and partial events.

1. **(0–2 min) Correct order.** P1 **puts down** the box, **then** picks up the black backpack.
   Leave ~10 seconds between the two actions.
2. **(2–4 min) Reversed order.** P1 picks up the black backpack **then** puts down the box.
   Same two actions, opposite sequence, same location.
3. **(4–6 min) Partial event — first half only.** P1 puts down the box and walks away. The
   second action **never happens**.
4. **(6–8 min) Partial event — second half only.** P1 picks up the black backpack. The first
   action never happens.
5. **(8–10 min) Correct order with a long gap.** The two actions ~90 seconds apart, other
   people passing between them.

**Produces:** `wrong_order` and `partial_event` challengers. Scenes 3 and 4 are the ones
systems fail on — they contain a *true* sub-event, so a naive matcher fires.

---

## 7. Session 6 — `sess_006` · 15 min · cam_02 · **test**

Bounded counts, occlusion, and track integrity. Use the pillar/column.

1. **(0–2 min) Clean two-person count.** Exactly 2 people, both fully visible the whole time,
   no occlusion. This is the *assessable* count.
2. **(2–4 min) Clean three-person count.** Exactly 3, all fully visible.
3. **(4–7 min) Two people with deliberate occlusion.** 2 people, one passes fully behind the
   pillar for ~8 seconds and re-emerges. Correct answer is still 2.
4. **(7–10 min) Three people with heavy occlusion.** 3 people repeatedly crossing behind the
   pillar and each other, such that at no single moment are all 3 cleanly separable. **The
   correct system behaviour here is `unresolved`, not a count.**
5. **(10–12 min) Track fragmentation decoy.** One person walks out of frame entirely and
   returns 40 seconds later wearing the **same clothes**. A naive tracker produces 2 tracklets
   for 1 person — the count must not become 2.
6. **(12–15 min) Duplicate-track decoy.** Two people in **identical** outfits walk together,
   separate around the pillar, and rejoin. Genuinely 2, easy to merge into 1.

**Produces:** `bounded_count_correct`, `bounded_count_incorrect`, `track_fragmentation`, and
the **duplicate-track decoy** (the one type missing from the current synthetic seed).

> Scenes 4, 5, 6 are the point of this session. Counting 2 people in an empty room proves
> nothing; refusing to count when the footage does not support it is the actual capability.

---

## 8. Session 7 — `sess_007` · 12 min · cam_02 · **dev**

Visible absence — **both variants, recorded as a matched pair.** §21.2 and §29 case 9 require
the assessable and the occluded counterpart together.

1. **(0–4 min) Assessable absence.** The full area is clearly visible, well lit, unoccluded,
   with **no black backpack anywhere in frame** for four continuous minutes. People walk
   through carrying nothing. This is a *certifiable* visible absence.
2. **(4–8 min) Occluded counterpart.** The **identical** framing, but a large object (the box
   on a table, or a person standing still) blocks a section of the area for the whole window.
   Still no black backpack visible — **but the region behind the occluder cannot be judged.**
   Correct behaviour is `unobservable`, **not** a clean negative.
3. **(8–10 min) Absence broken.** Same framing, and at ~9:00 someone carries the black backpack
   through. Absence is *false* here.
4. **(10–12 min) Dark absence.** Lights lowered. Nothing visible enough to certify either way.

**Produces:** `visible_absence_assessable`, `visible_absence_unassessable`, and a positive
control. Per [`ledger.md`](ledger.md) §4, `visible_absence_supported` is legal **only** when
`assessable=true` AND `observed_ticks_complete=true` — scenes 2 and 4 must fail that check.

---

## 9. Session 8 — `sess_008` · 10 min · cam_02 · **test**

Genuinely unobservable events. Lower the lights or shoot against a bright window.

1. **(0–3 min) Dark event.** P1 carries a bag across the area in light too low to determine its
   colour. The event happens; the *attribute* is unobservable.
2. **(3–6 min) Heavily occluded event.** P1 sets a bag down entirely behind the pillar. The
   approach and departure are visible; the placement is not.
3. **(6–8 min) Backlit.** P1 walks in front of a bright window, silhouetted. Shape visible,
   colour and detail not.
4. **(8–10 min) Partially assessable.** Lighting good enough for the object, not the attribute.

**Produces:** `unobservable` challengers across four distinct *reasons*. The reason matters —
G6 requires a reason-stratified undetermined rate, so do not blur these into one scene.

---

## 10. Session 9 — `sess_009` · 8 min · cam_01 · **train**

Bounded disjunction — "a person wearing a hat **or** a cap".

1. **(0–2 min)** P1 wearing the **cap** — first alternative true.
2. **(2–4 min)** P2 wearing the **wide-brim hat** — second alternative true.
3. **(4–6 min)** P3 wearing **neither** — both alternatives false.
4. **(6–8 min)** P1 (cap) and P2 (hat) **both** in frame — both alternatives true at once.

Repeat the same four-way structure with the **red / orange jacket** pair if time allows; it
gives a second disjunction family on a different attribute type.

**Produces:** `bounded_disjunction` challengers with all four truth combinations.

---

## 11. Session 10 — `sess_010` · 8 min · cam_01 · **train**

The abandonment distinction.

1. **(0–3 min) Short interruption — NOT abandonment.** P1 sets the black backpack down, steps
   ~3 metres away, stays **in frame and visibly attentive** for 45 seconds, returns, picks it
   up. This is *not* an abandoned bag.
2. **(3–6 min) True abandonment.** P1 sets the bag down and **leaves frame entirely**, not
   returning for the rest of the session. The bag remains visible, alone, for 3 minutes.
3. **(6–8 min) Ambiguous middle.** P1 sets the bag down and leaves frame for 40 seconds, then
   returns. Deliberately borderline — label it honestly as ambiguous rather than forcing it.

**Produces:** `short_interruption` challenger. Scene 3 is a legitimate ambiguity family, not a
mistake — record it and label it as such.

---

## 12. Session 11 — `sess_011` · 10 min · cam_03 · **test**

A genuinely empty search interval.

Point cam_03 at the quiet area and record 10 uninterrupted minutes in which **the target event
never occurs**. Normal incidental activity is fine and desirable — a completely dead frame is
a weaker test than a busy frame that simply never contains the queried event.

Target events that must **not** occur anywhere in these 10 minutes:

- anyone carrying a black backpack
- anyone wearing a hat or cap
- anyone putting an object down

**Produces:** `true_no_event` challengers and `cardinality=zero` test families.

> **This session imposes work on Person 1.** Per §21.5 and [`ledger.md`](ledger.md) §4, an
> empty-set **test** family requires `empty_set_review.review_complete=true` — meaning a human
> watched the **entire declared scope**, all 10 minutes, and certified the absence. It cannot
> be inferred, sampled, or skimmed. The same obligation applies to the four empty intervals in
> S1 and the one in S2.

---

## 13. Coverage checklist — tick before striking the set

Every item is required by §21.2 or §21.4. **Check these on site**, while re-recording is still
possible.

| Requirement | Session |
|---|---|
| Uninterrupted 60-minute fixed-camera file (G1) | S1 |
| Repeated event, ≥3 occurrences | S1 (4×) |
| Black-bag positive | S3 |
| Blue-bag wrong-attribute decoy | S3 |
| Wrong-object decoy | S2, S3 |
| Wrong-person / wrong-object binding | S4 |
| Wrong event order | S5 |
| Partial event | S5 |
| Dark or heavily occluded event | S8 |
| Genuinely empty search interval | S11, S1, S2 |
| Entrance → later exit-with-bag (cross-window) | S1 (~10 min gap) |
| Two- and three-person counts with deliberate occlusion | S6 |
| Bounded OR alternatives | S9 |
| Visible absence, clear **and** occluded | S7 |
| Short interruption vs. true abandonment | S10 |
| Visually similar actor | S3 |
| Track fragmentation | S6 |
| **Duplicate-track decoy** | S6 |

---

## 14. Immediately after recording (Person 5)

1. Confirm the G1 hour is a single file of ≥ 3600 s.
2. Compute streamed SHA-256 for every file:

```bash
find artifacts/authorized_footage -name '*.mp4' -exec sha256sum {} \;
```

3. Capture FFprobe data per file (codec, container, declared fps, timebase, duration).
4. Fill one manifest per session from the template into `data/manifests/<session_id>.json`,
   including authorization, consent, retention, collection date, and pool.
5. Seal each manifest with `eval.schema.seal_manifest(...)`. **Never hand-write
   `content_hash`.**
6. Only then does Person 1 start watching.

Manifests are immutable once sealed. A later edit changes the hash and validation rejects it —
that is the point.
