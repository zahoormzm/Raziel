# START HERE — what we need from you, and why

**You do not need to have read anything else.** This document is self-contained. It explains
what the project is, why it is currently stuck, what we are asking you to do, and what to
expect while doing it.

Read time: about 15 minutes. Please read it all before recording day — the mistakes that cost
us most are the ones that cannot be fixed afterwards, and they are all avoidable.

---

## 1. What we are building, in plain language

We are building a system called **RAZIEL** that searches recorded security-camera footage
using ordinary English.

You type something like *"a person carrying a black backpack near the gate"*, and it tells you
whether that happened in the footage you pointed it at, when it happened, and shows you the
clip. It can find zero, one, or many occurrences.

The unusual part — and the whole point of the project — is that **it is built to admit when it
does not know.** Most systems of this kind will always return *something*, because returning
nothing looks like failure. Ours distinguishes between four genuinely different answers:

| Answer | Means |
|---|---|
| **Supported** | We can see it. Here is the evidence. |
| **Contradicted** | We can see clearly that it did **not** happen. |
| **Unobservable** | The footage cannot show us. Too dark, blocked, out of frame. |
| **Undetermined** | We ran out of time or budget, or something failed. We did not decide. |

The third and fourth are the interesting ones. *"The camera couldn't see it"* is a completely
different statement from *"it didn't happen"* — and a system that blurs those two is dangerous
in exactly the situations where people rely on it most.

---

## 2. Why we are stuck

The software is built. It runs. 181 tests pass.

But we have no idea whether it is any **good**, because we have never shown it real footage.

Everything it has been tested on so far is *synthetic* — computer-generated placeholder video
and computer-generated placeholder labels. That was the right way to build it: it proves the
plumbing works. It proves nothing about whether the system can actually find a black backpack.

To find out, we need two things that only humans can produce:

1. **Real footage** in which we know exactly what happens, because we staged it.
2. **A written record of exactly what happens in it** — produced by a person watching, not by
   the system.

That second thing is the critical bit. If we let the system tell us what is in the footage and
then score the system against its own answers, we learn nothing. The human record must come
first and must be independent. We call it **the ledger**.

**Six separate quality checkpoints in this project are blocked on this one input.** Nothing
downstream can move until it exists. That is why you are being asked to spend a day recording
and a couple of days watching video.

---

## 3. The words we use

You will meet these terms constantly. Learn these eight and everything else follows.

**Footage / session.** One recording session — one camera, one continuous scenario. Each gets
an id like `sess_003`.

**The ledger.** The human-written record of everything notable that happens in the footage.
One entry per event: which camera, start and end time, who was involved, what objects, what
actions, how the lighting was, whether anything was blocked from view. **The ledger is the
truth.** Everything else is measured against it.

**Query family.** One test case. It bundles together: a question in English ("*a black
backpack near the gate*"), two reworded versions of that same question written by two
different people, which footage it applies to, and the correct answer taken from the ledger.
We need about 60 of these.

**Paraphrase.** A reworded version of the same question. *"show me a dark rucksack by the
gate"* is a paraphrase of the above. We need **two per family, written by two different people
who have not seen each other's wording.** This tests that the system understands meaning
rather than memorising exact phrasing.

**Challenger.** A deliberate near-miss — something that looks almost right but is wrong, that
a good system must reject. If the real event is a **black** bag, the challenger is the same
scene with a **blue** bag. Challengers are where systems actually fail, so we stage them on
purpose.

**Anonymous actor.** In the ledger, people are `P1`, `P2`, `P3` — never names, never facial
descriptions. This is a hard rule, explained in §8.

**Assessable.** Whether a human watching could actually judge the thing. If someone walks
through a dark corridor and you genuinely cannot tell what colour their bag is, that is
**unassessable** — and the honest label is "unobservable", not a guess.

**Split.** We divide our test cases into three piles: **train** (used to build), **dev** (used
to tune settings), and **test** (used *once*, at the end, to report honest results). The test
pile must never be used for tuning — otherwise our final numbers are a lie we told ourselves.

---

## 4. What we are asking you to do

Three phases, five people. Roughly one day of recording and two to three days of watching and
writing.

### Phase 1 — Record (about one day, everyone)

We stage about **three hours of footage across 11 sessions** with three fixed cameras. Every
scene exists to test one specific thing. A detailed scene-by-scene script exists — you will be
handed it on the day and it reads like a shot list.

One session matters more than all the others: **an uninterrupted 60-minute recording from a
camera that does not move.** See §6.

### Phase 2 — Watch and write the ledger (about two days, Persons 1 and 4)

Someone watches every minute of footage and writes down every notable event. This is slow,
unglamorous, and completely irreplaceable.

### Phase 3 — Build the test cases (about two days, Persons 1, 2, 3, 4)

Turn ledger entries into query families: write the questions, write two independent
paraphrases each, and have two people independently label a subset so we can measure how much
humans agree with each other.

### Phase 4 — The generalization probe (about half a day, Persons 5 then 1)

A small amount of **footage we did not record ourselves**, labelled with our own protocol.
This is optional in the sense that the project survives without it, and important in the sense
that it answers the sharpest question anyone will ask. See §4a.

---

## 4a. The generalization probe — footage we did not record

### The problem it fixes

Everything in Phases 1–3 has a weakness that is worth stating out loud, because someone else
will state it for us otherwise:

> **We stage the events, we write the questions, and we write the answers.**

Three degrees of freedom, all held by the same five people. Even with everyone acting in good
faith, we will unconsciously stage scenes the way we imagine the system sees the world, and
write questions in the vocabulary we used when staging. A reviewer is entitled to ask: *"Does
this work on footage you didn't design to be findable?"* — and right now we could not answer.

So we take a **small amount of third-party footage**, label it with our own protocol, and
report it separately. If the system does well, that is real evidence. If it does badly, that
is a real finding and we publish it. Either outcome beats having no answer.

### The one rule

> **Their video. Our labels. Always.**

Public datasets come with their own annotations. **We never use them.** Not as a shortcut, not
as a starting point, not "just to check". Three reasons:

1. **Their label model is not ours.** We record four evidence states, assessability, boundary
   tolerance, bounded counts, and visible-absence safety conditions. No public dataset has
   those fields. There is nothing to import.
2. **We do not know their protocol.** Who labelled it, how carefully, under what definition of
   "carrying"? Unknown provenance cannot be ground truth.
3. **It is the one rule this whole lane exists to protect.** Our labels come from a human
   watching video. That is the entire basis of every number we will publish.

The software enforces this: an `external` family whose ground truth is not `human_ledger` is
rejected by the test suite.

### Choosing the footage — consent comes first

**This is the part to get right, and the part where the obvious shortcut is the wrong one.**

Surveillance datasets have a genuinely bad history. Several well-known ones were built from
footage of people who never agreed to be recorded, labelled, or published, and were later
withdrawn. For a project whose whole argument is *"we are the system that does not overclaim
about people, does not assert identity, and admits what it cannot see"*, evaluating on
non-consensual surveillance footage would undercut the thing we are selling. A sharp reviewer
would notice.

So the selection order is **consent provenance first, convenience second**:

1. **Best:** a dataset recorded with **consenting paid actors** for research purposes. Some
   surveillance-activity datasets were built exactly this way, specifically to avoid the
   consent problem. This matches our own staged model and is easy to defend.
2. **Acceptable:** clearly licensed footage (e.g. a permissive Creative Commons licence) where
   the licence and its provenance are documented. Less surveillance-shaped, still useful.
3. **Not acceptable:** anything scraped, anything with unclear provenance, anything whose
   consent status you cannot establish, and anything a person could be identified from and did
   not agree to.

**Person 5 must read the actual licence and consent documentation before downloading
anything.** Do not rely on "everyone uses it" or on a summary — including any summary given to
you inside this project. Terms change and datasets get withdrawn. Write down what you found.

If you cannot establish consent provenance in 30 minutes of looking, **pick a different
source**. This is not the place to push through ambiguity.

### What to actually do

**Person 5:**

1. Identify a candidate source. Record its name, URL, licence, consent basis, and the date you
   checked, in the session notes.
2. Take **20–30 minutes** of footage. Not more — this is a probe, not a second dataset, and
   every extra minute is ledger time taken from Person 1.
3. Prefer fixed-camera, surveillance-like footage. Handheld or heavily edited video tests
   something we do not claim to do.
4. Place it under `artifacts/authorized_footage/external/session_201/`.
5. Seal the manifest with `--authorization-status` reflecting the **licence**, not staged
   consent, and put the licence reference in the notes. Consent here means *the source
   documented its consent basis*, not that we obtained it.

**Person 1:**

6. Ledger it **exactly like our own footage**. Same protocol, same anonymous `P1`/`P2` ids,
   same assessability discipline. Do not look at any annotations that shipped with the
   dataset — if the download included label files, do not open them.
7. Build **5–10 families** from your ledger entries. Persons 2 and 3 write paraphrases as
   normal.

**Everyone:** these families go in their own pool and their own split. They are reported
**separately** from the staged results — never averaged together. A combined number would hide
exactly the difference we built this to measure.

### What to expect

Expect the system to do **worse** here than on staged footage. That is the normal and
informative outcome, not a failure of the probe. Staged footage is clean, well-framed, and
deliberately unambiguous; real footage is not. The size of the gap is the finding.

Budget: about **2 hours** of Person 1's time on top of the main ledger. If Phase 2 has
overrun, this is the first thing to cut — say so, and we report the staged results alone with
the limitation stated plainly.

---

## 5. Who does what

Five roles. **The separation between them is the entire point** — it is what makes our quality
measurements meaningful. If two of these roles collapse into one person, the measurement
becomes worthless, not just weaker.

| You are | You do | You must NOT |
|---|---|---|
| **Person 1** — Ledger author | Watch every file completely. Write one entry per notable event. Write the main question for each test case. | Take part in the blind labelling — you know all the answers |
| **Person 2** — Paraphrase A | Write the first reworded version of every question. Later, independently label a subset. | See Person 3's wording or labels |
| **Person 3** — Paraphrase B | Write the second reworded version of every question. Later, independently label the same subset. | See Person 2's wording or labels |
| **Person 4** — Adjudicator | After Persons 2 and 3 have both finished and their work is saved, settle any disagreements. Also help Person 1 with the ledger. | Look at either person's labels before **both** are finished and saved |
| **Person 5** — Data steward | Run the cameras. Handle consent. Compute file fingerprints. Fill in the records. Run the system. | Write any of the ground truth |

**Why Persons 2 and 3 must not talk.** We are measuring how often two careful humans, working
separately, reach the same answer. If they discuss it first, they will agree — and we will
have measured nothing. Their disagreements are *valuable data*: they tell us which questions
are genuinely ambiguous, which is exactly what we need to know.

**Why Person 4 waits.** Same reason. An adjudicator who peeks at the first person's answer
before the second finishes has contaminated the comparison. The software actually enforces
this — it records timestamps and will reject an adjudication that was filed too early. Not
because we distrust you, but because in three months nobody will remember what order things
happened in, and the timestamps will.

---

## 6. The one thing that must not go wrong

**We need one continuous 60-minute video file from a camera that does not move.**

Not two 30-minute files. Not 59 minutes. One file, at least 3600 seconds long, from a mounted
camera that nobody touches for the entire hour.

**Why.** We need to prove the system can ingest a long recording without its internal clock
drifting or jumping backwards, and that if it crashes halfway it can resume without
double-counting. That test is meaningless on short clips — the errors we are hunting only
appear over long durations.

**Why it is fragile.** Most cameras and phones silently split long recordings into chunks —
either at a 4 GB file-size limit, or a flat 30-minute cap. They do not warn you. You get back
what looks like an hour and is actually two files with a gap between them, and a gap fails the
test outright.

**What to do about it:**

1. **Do a 10-minute test recording days beforehand.** Check the resulting file. If your camera
   splits at 10 minutes it will certainly split at 60.
2. If the camera splits: format the memory card as **exFAT** (not FAT32), and turn off any
   auto-split, loop-record, or auto-power-off setting.
3. Safest option: record straight to a laptop from a USB camera using ffmpeg. That guarantees
   one file. The exact command is in the recording protocol.
4. **Check the duration before you take the camera down.** If it reads 1800 or 2700 instead of
   3600, re-record immediately — while the set is still standing and everyone is still there.

Other rules for that hour: mount the camera and never touch it, lock the exposure and white
balance if you can (auto-exposure hunting creates brightness jumps that look like scene
changes), and let it run to the end even if the scene is empty. **Empty stretches are
useful** — we specifically need intervals where genuinely nothing happens.

---

## 7. What to expect — an honest schedule

| Phase | Who | Time | What it actually feels like |
|---|---|---|---|
| Prep | Person 5 | Half a day | Camera tests, consent forms, gathering props |
| Recording | Everyone | One full day | Like a low-budget film shoot. Lots of "again, but slower" |
| **Ledger** | Persons 1 + 4 | **9–10 hours total, split between two people** | The hard part. See below |
| Writing questions | Person 1 | Half a day | Enjoyable — you are inventing test cases |
| Paraphrases | Persons 2, 3 | 2–3 hours each | Easy, done separately |
| Blind labelling | Persons 2, 3 | 2–3 hours each | Careful work on 14 cases |
| Adjudication | Person 4 | 2 hours | Only after both of the above are saved |

**Set expectations honestly about the ledger.** Three hours of footage takes roughly **three
times its length** to log properly, because you pause, rewind, and write. That is 9–10 hours.
Splitting it between Persons 1 and 4 makes it about five hours each — a long day, not a lost
week.

It is also a **hard serial dependency**: no ledger means no test cases, which means no
evaluation, which means no results. Everything waits on it. Please do not schedule it as
"whenever someone gets around to it".

**Empty stretches still have to be watched.** Some of our test cases assert that something
never happened during a specific window. The only way to certify that honestly is for a human
to watch the entire window. You cannot skim, sample, or skip ahead. It is boring, and it is
the whole basis of the claim.

---

## 8. Rules that are not negotiable, and why

These are not bureaucracy. Each one exists because breaking it would make us publish something
untrue.

**People are `P1`, `P2`, `P3` — never names, never faces.** The system does not do facial
recognition and does not claim to identify anyone. It tracks "a person" within a single camera
over a short continuous period, and nothing more. If our notes say "that's Dave", we have
quietly built something we said we did not build. Write "person in a red jacket" rather than
anything that identifies a specific individual.

**Consent comes before recording, in writing, from every visible participant.** It covers how
long we keep the footage. If someone changes their mind afterwards, that session gets deleted
and the record marked accordingly — not quietly reused.

**Footage never goes into the shared code repository.** Code history cannot be reliably erased.
Video of real consenting people does not belong in it. The footage lives in a local folder that
is deliberately excluded; only the *record about* the footage — its fingerprint, its consent
status, its retention policy — gets committed. That way, if the footage is deleted, we can
still prove what we collected and under what terms.

**Nobody invents a number, a label, or a result.** If we did not measure it, it says "not yet
measured". There are no placeholder scores anywhere in this project and there will not be. This
is worth stating plainly because it is unusual, and because under time pressure the temptation
is real. A missing number is honest. A made-up one is not, and it will be found.

**Never let the system tell us the answer.** If we score the system against labels the system
produced, we have measured nothing but its self-consistency. The ledger comes from human eyes
watching video. That is the only source.

---

## 9. What "done" looks like

You are finished with your part when all of these are true:

- [ ] ~3 hours of footage across 11 sessions exists in the footage folder
- [ ] One of those files is a single unbroken recording of at least 3600 seconds
- [ ] Every participant consented in writing, before recording
- [ ] Every session has a completed record: fingerprint, date, camera, consent, retention
- [ ] A human has watched **every minute** and written the ledger
- [ ] About 60 test cases exist, each with a question and two independently written paraphrases
- [ ] 14 of them have been independently labelled by two people who did not confer
- [ ] Those disagreements have been settled by a third person, afterwards
- [ ] Nothing anywhere is marked as synthetic
- [ ] *(If done)* 20–30 min of external footage, its licence and consent basis written down,
      5–10 families built from **our** ledger, kept in its own pool and reported separately

Then the technical work resumes and we finally find out whether the thing we built works.

---

## 10. Common confusions

**"Why stage the footage instead of using real security footage?"** Because we need to *know*
the right answer with certainty. With found footage, nobody knows the ground truth, so we
cannot score anything. Staging is what makes measurement possible. We also do not have the
right to label and publish results about people who did not consent.

**"Why two paraphrases? Isn't one question enough?"** Because we need to know the system
understood the *meaning*, not that it matched some words. If it finds the bag when asked for a
"black backpack" but fails on "dark rucksack", that is a real and important weakness.

**"Why deliberately record things that are too dark to see?"** Because "I can't tell" is a
correct answer we specifically want the system to give. If we only ever test it on clear
footage, we never find out whether it bluffs when the footage is bad — which is precisely when
bluffing does damage.

**"Why does it matter if the count is wrong by one?"** It usually does not — which is why the
system is designed to say *"I can't count reliably here"* rather than guess. We stage scenes
where people walk behind a pillar specifically to check it refuses to count instead of
guessing.

**"Can I just fix a mistake in the record afterwards?"** No — records are sealed with a
fingerprint when created, and editing one breaks the seal and gets rejected. If something is
wrong, we add a correction rather than rewriting history. This is deliberate: it means nobody
can quietly change what the ground truth said after seeing the results.

**"What if we run out of time?"** Tell us early. We have a hard floor of 40 test cases and a
target of 60. Dropping from 60 to 45 is a manageable, reportable decision. Rushing 60 sloppy
ones is not — bad labels are worse than fewer labels, because they produce confident wrong
numbers instead of honest smaller ones.

---

## 11. Where to go next

| You are | Read next |
|---|---|
| Person 5 (running the cameras) | `data/footage_collection_protocol.md`, then `data/footage_shotlist.md` |
| Person 1 or 4 (ledger) | `data/ledger.md` |
| Person 2 or 3 (paraphrases, labelling) | `data/ledger.md` §3 and §6 |
| Curious about the whole design | `RAZIEL_Master_Execution_Plan_v1.3.md` (long) |

Questions are cheaper than re-recording. Ask before the shoot, not after.
