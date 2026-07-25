// Every number here is a measured result read from this repository's own
// benchmark reports. Nothing is estimated, rounded up, or invented. Where a
// thing has not been measured, it says so.
//
// Sources: artifacts/b1/report.json, artifacts/b2/report.json,
// artifacts/b5/report.json, artifacts/b6/report.json,
// eval/reports/g3_audit.json, RELEASE_STATUS.md

export const evidenceStates = [
  {
    id: "supported",
    name: "Supported",
    body: "The constraint is visible in the footage, and the frames that show it are attached to the result.",
    note: "Evidence, not confidence.",
  },
  {
    id: "contradicted",
    name: "Contradicted",
    body: "The footage clearly shows the constraint is not met. The bag is there, and it is blue.",
    note: "A real answer, not a failure.",
  },
  {
    id: "unobservable",
    name: "Unobservable",
    body: "The camera could not show it. Too dark, occluded, or out of frame. The question stays open.",
    note: "Not the same as absent.",
  },
  {
    id: "undetermined",
    name: "Undetermined",
    body: "The system ran out of budget, or a component failed. No judgement was reached at all.",
    note: "Ours to fix, not yours to trust.",
  },
];

export const stages = [
  {
    name: "Parse",
    title: "Read the request literally",
    body: "The query becomes typed atoms, relations and bounded logic. Every atom maps to a span of what you actually wrote. The parser cannot invent an attribute you did not ask for, and unsupported grammar produces one focused question rather than a guess.",
    guardLabel: "Refuses",
    guard: "Open-world negation, universal quantification, comparatives and superlatives, and same-person identity across a long gap.",
  },
  {
    name: "Retrieve",
    title: "Cast the widest safe net",
    body: "Every enabled channel scores every embedded sampling tick in the declared scope. A candidate exists if any channel clears its threshold; ranking reorders that set but never shrinks it.",
    guardLabel: "Invariant",
    guard: "Rank fusion orders candidates. It never decides whether a candidate exists.",
  },
  {
    name: "Assemble",
    title: "Build episodes across windows",
    body: "Ordered events are joined into episodes within one camera and session, respecting maximum gaps. The join is bounded, and hitting that bound is recorded rather than hidden.",
    guardLabel: "Records",
    guard: "A truncated assembly is reported incomplete. It can never read as a complete search that found nothing.",
  },
  {
    name: "Verify",
    title: "Check each constraint on its own",
    body: "A vision-language model examines the candidate frames and resolves every required constraint independently to one of the four states, citing the frames it used.",
    guardLabel: "Preserves",
    guard: "Unobservable and undetermined stay distinct. One is a footage problem, the other is ours.",
  },
  {
    name: "Ground",
    title: "Point at the evidence",
    body: "Spatial grounding runs only on candidates that already verified, and only where every referenced object can be located. Partial grounding is dropped rather than shown half-drawn.",
    guardLabel: "Fallback",
    guard: "If the boxes cannot be trusted, the clip is shown without them and that is stated.",
  },
  {
    name: "Export",
    title: "Leave a traceable record",
    body: "An evidence clip is cut on keyframe boundaries with a manifest recording source hash, output hash, model revisions, operating point, and the exact interval extracted.",
    guardLabel: "Claims",
    guard: "A traceable extraction record. Not a claim of legal admissibility or tamper-proofness.",
  },
];

export const metrics = [
  {
    value: 22.8,
    decimals: 1,
    unit: "×",
    label: "Faster than real time, indexing",
    note: "B1 · 600/600 ticks · 0 failures",
  },
  {
    value: 129.9,
    decimals: 1,
    unit: "fps",
    label: "Frame embedding throughput",
    note: "B1 · SigLIP2 · 1.66 GB peak VRAM",
  },
  {
    value: 0.18,
    decimals: 2,
    unit: "ms",
    label: "Query parse, warm median",
    note: "B5 · 24 warm runs · p95 0.32 ms",
  },
  {
    value: 4.64,
    decimals: 2,
    unit: "GiB",
    label: "Verifier peak memory",
    note: "B2 · NF4 · 5.78 GiB headroom",
  },
  {
    value: 96.8,
    decimals: 1,
    unit: "%",
    label: "Parser correctness, 31 queries",
    note: "G3 · 0 unsafe failures",
  },
  {
    value: 0,
    decimals: 0,
    unit: "",
    label: "Placeholder scores in this repository",
    note: "Unmeasured reads not_yet_measured",
  },
];

// Measured, and kept disabled because of what the measurement said.
export const rejectedLane = {
  name: "Qwen3-VL 8B verifier",
  verdict: "Measured, not shipped",
  body: "The larger verifier is fast enough — a 12-second candidate verifies in 14.57 s against a 20 s ceiling, with zero retries across 30 warm runs. It fails on memory: 7.92 GiB peak leaves 1.32 GiB headroom on a 12 GiB card, under the 1.5 GiB floor. So it stays disabled and remains a reproducible ablation rather than a quiet default.",
  figures: [
    { k: "Warm median, 12 s candidate", v: "14.57 s" },
    { k: "Peak VRAM", v: "7.92 GiB" },
    { k: "Minimum headroom", v: "1.32 GiB" },
    { k: "Required headroom", v: "1.50 GiB" },
  ],
};

export const gates = [
  { id: "G1", name: "Ingestion", tone: "partial", state: "Partially proven", detail: "PTS-safe decode, restart and coverage tests pass. The authorized one-hour ingest is still required." },
  { id: "G2", name: "Indexing", tone: "partial", state: "Partially proven", detail: "Throughput and exact-score coverage pass. Tracklet quality needs a hand-labelled authorized subset." },
  { id: "G3", name: "Parsing", tone: "pass", state: "Passed", detail: "31 scripted queries across 16 categories, 96.8% fully correct, zero unsafe parses." },
  { id: "G4", name: "Retrieval", tone: "open", state: "Open", detail: "Thresholds stay null until they are selected from real development measurements." },
  { id: "G5", name: "Assembly", tone: "partial", state: "Fixture passed", detail: "Order, binding, scope and episode-cap behaviour are tested on synthetic fixtures." },
  { id: "G6", name: "Verification", tone: "open", state: "Open", detail: "Hardware feasibility proven. Semantic macro-F1 needs real footage and real labels." },
  { id: "G7", name: "Grounding", tone: "open", state: "Open", detail: "Safety behaviour tested. Disabled until measured on authorized footage." },
  { id: "G8", name: "Export", tone: "partial", state: "Passed on synthetic", detail: "Manifest hashing and resolver boundaries pass. Repeat on the frozen authorized archive." },
  { id: "G9", name: "Dataset", tone: "open", state: "Open", detail: "Schemas, protocol and validators exist. Awaiting authorized footage and a human ledger." },
];

export const claims = [
  {
    title: "Scoped, not absolute",
    body: "Every result is conditional on the declared cameras, time range, sampling policy and verification budget. Change the scope and the answer may change.",
  },
  {
    title: "Anonymous by construction",
    body: "Tracklets are scoped to one camera and one session. The system does not do face recognition and makes no claim about who anyone is.",
  },
  {
    title: "Absence is earned",
    body: "Visible absence is only certifiable inside a continuous, assessable interval with complete coverage. Otherwise the honest answer is unobservable.",
  },
  {
    title: "Counting is refusable",
    body: "Counts run over qualifying tracklets in one continuous interval, and become unresolved under heavy occlusion or fragmentation rather than guessing.",
  },
];

export const refusals = [
  "Rendering a budget-truncated search as a clean no-match",
  "Turning missing coverage into proven absence",
  "Claiming every source frame was watched",
  "Asserting identity across cameras or long gaps",
  "Reporting a confidence number it has not calibrated",
  "Presenting an export manifest as legal admissibility",
];
