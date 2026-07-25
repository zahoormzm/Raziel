const byId = (id) => document.getElementById(id);

async function loadBrand() {
  const response = await fetch("/config/brand", { cache: "no-store" });
  if (!response.ok) throw new Error("Shared brand configuration is unavailable.");
  const brand = await response.json();
  byId("product-name").textContent = brand.product_name;
  byId("product-subtitle").textContent = brand.product_subtitle;
  byId("retrieval-name").textContent = `${brand.retrieval_name} / inactive`;
  document.title = `${brand.product_name} — ${brand.product_subtitle}`;
}

async function loadHealth() {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const health = await response.json();
    byId("worker-state").textContent = health.worker_state.toUpperCase();
  } catch {
    byId("worker-state").textContent = "UNAVAILABLE";
  }
}

async function loadCoverage() {
  const response = await fetch("/coverage", { cache: "no-store" });
  if (!response.ok) throw new Error("Archive coverage is unavailable.");
  const report = await response.json();
  const sources = report.sources || [];
  const duration = sources.reduce((sum, source) => sum + Number(source.duration_s || 0), 0);
  byId("archive-duration").textContent =
    sources.length ? `${sources.length} source(s) / ${formatTime(duration)}` : "Not ingested";
  const expected = sources.reduce((sum, source) => sum + Number(source.expected_ticks || 0), 0);
  const embedded = sources.reduce((sum, source) => sum + Number(source.embedded_ticks || 0), 0);
  byId("index-health").textContent =
    expected ? `${Math.round((embedded / expected) * 100)}% embedded coverage` : "No coverage report";

  const select = byId("camera");
  const cameras = [...new Set(sources.map((source) => source.camera_id).filter(Boolean))].sort();
  for (const camera of cameras) {
    const option = document.createElement("option");
    option.value = camera;
    option.textContent = camera;
    select.append(option);
  }
  updateScopeStatement();
}

async function loadBenchmark() {
  const response = await fetch("/benchmark/current", { cache: "no-store" });
  if (!response.ok) throw new Error("Benchmark panel is unavailable.");
  const report = await response.json();
  const measured = Object.values(report.headline || {}).filter(
    (value) => typeof value === "number",
  ).length;
  byId("operating-point").textContent =
    measured > 0
      ? `${report.primary_config || "current"} / ${measured} held-out metrics`
      : "Held-out metrics / not yet measured";
}

function updateScopeStatement() {
  const camera = byId("camera").value || "all declared cameras";
  const start = byId("start-time").value || "archive start";
  const end = byId("end-time").value || "archive end";
  byId("scope-statement").textContent =
    `Declared scope: ${camera}, ${start} → ${end}. Sampling policy is shown with results.`;
}

function renderInterpretation(plan) {
  byId("intent-empty").hidden = true;
  const chips = byId("intent-chips");
  chips.replaceChildren(
    ...(plan.atoms || []).map((atom) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = `${atom.type}: ${atom.text_span}`;
      return chip;
    }),
  );
  if (plan.state === "clarification_required") {
    byId("headline").textContent = plan.clarification_question || "Clarification required.";
  }
}

async function loadInterpretation(body) {
  const response = await fetch("/query/interpret", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error("The bounded parser is unavailable.");
  const plan = await response.json();
  renderInterpretation(plan);
  return plan;
}

const delay = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

async function waitForQuery(job) {
  if (["complete", "failed", "cancelled"].includes(job.state)) return job;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await delay(500);
    const response = await fetch(`/query/${job.job_id}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Search progress endpoint is unavailable.");
    job = await response.json();
    byId("headline").textContent =
      job.state === "running"
        ? "Exact scoring, temporal assembly, and verification routing are running…"
        : "Search queued.";
    if (["complete", "failed", "cancelled"].includes(job.state)) return job;
  }
  throw new Error("Search exceeded the disclosed 60-second live ceiling.");
}

function formatTime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(value / 60);
  const remainder = value - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(1).padStart(4, "0")}`;
}

function createConstraintRow(constraint) {
  const row = document.createElement("div");
  row.className = "constraint-row";
  const name = document.createElement("span");
  name.textContent = constraint.constraint_id || "constraint";
  const state = document.createElement("span");
  state.className = `constraint-state ${constraint.state || "undetermined"}`;
  state.textContent = constraint.state || "undetermined";
  row.append(name, state);
  return row;
}

function inspectEvidence(result, outcome, category) {
  const inspector = byId("evidence-inspector");
  inspector.hidden = false;
  byId("evidence-title").textContent =
    `${category.replaceAll("-", " ").toUpperCase()} / ${outcome.camera_id || outcome.video_id}`;
  byId("evidence-interval").textContent =
    `${formatTime(outcome.t0)} → ${formatTime(outcome.t1)} · ${outcome.video_id}`;
  byId("constraint-list").replaceChildren(
    ...(outcome.constraints || []).map(createConstraintRow),
  );
  const trace = byId("trace-list");
  const lanes = (outcome.retrieval_lanes || []).join(", ") || "not reported";
  const nodes = (outcome.graph_node_ids || []).join(", ") || "none";
  const edges = (outcome.graph_edge_ids || []).join(", ") || "none";
  trace.textContent = `Retrieval lanes: ${lanes}\nGraph nodes: ${nodes}\nGraph edges: ${edges}`;
  trace.style.whiteSpace = "pre-line";

  const player = byId("evidence-player");
  if (outcome.preview_url) {
    player.src = outcome.preview_url;
    player.classList.add("has-source");
    byId("player-placeholder").hidden = true;
  } else {
    player.removeAttribute("src");
    player.classList.remove("has-source");
    byId("player-placeholder").hidden = false;
  }
  byId("preview-export").onclick = () => requestExport(result, outcome, "preview");
  byId("evidence-export").onclick = () => requestExport(result, outcome, "evidence");
  inspector.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function requestExport(result, outcome, mode) {
  const response = await fetch("/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      search_id: result.search_id,
      match_id: outcome.candidate_id,
      mode,
    }),
  });
  const job = await response.json();
  if (job.state === "failed") {
    byId("trace-list").textContent =
      `Export unavailable: ${job.error || "local export handler is not configured"}`;
  } else if (job.result) {
    const message = document.createElement("span");
    message.textContent = `${mode} export ready. `;
    const clip = document.createElement("a");
    clip.href = job.result.clip_url;
    clip.textContent = "Download clip";
    const manifest = document.createElement("a");
    manifest.href = job.result.manifest_url;
    manifest.textContent = "View manifest";
    byId("trace-list").replaceChildren(message, clip, document.createTextNode(" · "), manifest);
  }
}

function createResultCard(result, outcome, category) {
  const card = document.createElement("article");
  card.className = `result-card ${category}`;
  const interval = document.createElement("span");
  interval.className = "result-time";
  interval.textContent = `${formatTime(outcome.t0)}–${formatTime(outcome.t1)}`;
  const meta = document.createElement("div");
  meta.className = "result-meta";
  const heading = document.createElement("strong");
  heading.textContent =
    `${outcome.camera_id || "UNASSIGNED CAMERA"} · ${outcome.video_id}` +
    (outcome.verification_cached ? " · CACHED VERIFICATION" : "");
  const summary = document.createElement("span");
  summary.textContent =
    outcome.rationale ||
    `${(outcome.constraints || []).length} constraint states · ${(outcome.retrieval_lanes || []).length} retrieval lanes`;
  meta.append(heading, summary);
  const inspect = document.createElement("button");
  inspect.type = "button";
  inspect.textContent = "Inspect evidence";
  inspect.addEventListener("click", () => inspectEvidence(result, outcome, category));
  card.append(interval, meta, inspect);
  return card;
}

function renderTimeline(result, outcomes) {
  const cameras = new Map();
  for (const outcome of outcomes) {
    const camera = outcome.camera_id || outcome.video_id || "UNASSIGNED";
    if (!cameras.has(camera)) cameras.set(camera, []);
    cameras.get(camera).push(outcome);
  }
  if (!cameras.size) return;
  const maxTime = Math.max(
    1,
    ...outcomes.map((item) => Number(item.t1) || 0),
    Number(result.scope?.end_time) || 0,
  );
  const timeline = byId("timeline");
  timeline.querySelectorAll(".camera-lane").forEach((lane) => lane.remove());
  for (const [camera, cameraOutcomes] of cameras) {
    const lane = document.createElement("div");
    lane.className = "camera-lane";
    const label = document.createElement("span");
    label.className = "camera-name";
    label.textContent = camera;
    const track = document.createElement("div");
    track.className = "track";
    for (const outcome of cameraOutcomes) {
      const marker = document.createElement("span");
      marker.className = "match-marker";
      marker.style.left = `${Math.min(100, (outcome.t0 / maxTime) * 100)}%`;
      marker.style.width =
        `${Math.max(0.4, ((outcome.t1 - outcome.t0) / maxTime) * 100)}%`;
      marker.title = `${formatTime(outcome.t0)}–${formatTime(outcome.t1)}`;
      track.append(marker);
    }
    lane.append(label, track);
    timeline.append(lane);
  }
}

function renderJob(job) {
  if (job.state === "failed") {
    byId("headline").textContent = "SYSTEM COULD NOT START THE SEARCH";
    const error = document.createElement("article");
    error.className = "error-card";
    error.textContent = job.error || "The local pipeline did not return a result.";
    byId("result-list").replaceChildren(error);
    return;
  }
  const result = job.result;
  if (!result) {
    byId("headline").textContent = "Search queued.";
    return;
  }
  byId("headline").textContent = result.headline || "Result received.";
  if (result.interpretation?.atoms?.length) {
    byId("intent-empty").hidden = true;
    byId("intent-chips").replaceChildren(
      ...result.interpretation.atoms.map((atom) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = `${atom.type}: ${atom.text_span}`;
        return chip;
      }),
    );
  }
  byId("progress-scored").textContent =
    `${result.indexing?.embedded_ticks ?? 0}/${result.indexing?.expected_ticks ?? 0}`;
  byId("progress-windows").textContent =
    result.candidate_generation?.qualifying_windows ?? 0;
  byId("progress-episodes").textContent = result.assembly?.episodes_generated ?? 0;
  byId("progress-verified").textContent =
    `${result.verification?.clusters_verified ?? 0}/${result.verification?.clusters_total ?? 0}`;
  byId("progress-scope").textContent = "RESOLVED";
  byId("index-health").textContent =
    `${Math.round((result.indexing?.scored_coverage ?? 0) * 100)}% embedded coverage`;
  byId("retrieval-name").textContent =
    `${byId("retrieval-name").textContent.split(" / ")[0]} / complete`;

  const groups = [
    ["verified", result.verified_matches || []],
    ["unresolved-visual", result.unresolved_visual || []],
    ["unresolved-system", result.unresolved_system || []],
    ["rejected", result.rejected_near_misses || []],
  ];
  const cards = groups.flatMap(([category, items]) =>
    items.map((outcome) => createResultCard(result, outcome, category)),
  );
  if (cards.length) byId("result-list").replaceChildren(...cards);
  renderTimeline(result, groups.flatMap(([, items]) => items));
}

async function submitSearch(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const query = byId("query").value.trim();
  if (!query) return;
  updateScopeStatement();
  button.disabled = true;
  byId("headline").textContent = "Resolving scope and parsing constraints…";
  byId("progress-scope").textContent = "RESOLVING";
  try {
    const body = {
      text: query,
      camera_ids: byId("camera").value ? [byId("camera").value] : [],
      start_time: byId("start-time").value || null,
      end_time: byId("end-time").value || null,
      budgets: { disclosed: true },
    };
    await loadInterpretation(body);
    const response = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Query endpoint returned ${response.status}.`);
    renderJob(await waitForQuery(await response.json()));
  } catch (error) {
    renderJob({ state: "failed", error: String(error) });
  } finally {
    button.disabled = false;
  }
}

for (const id of ["camera", "start-time", "end-time"]) {
  byId(id).addEventListener("change", updateScopeStatement);
}
byId("search-form").addEventListener("submit", submitSearch);

Promise.allSettled([loadBrand(), loadHealth(), loadCoverage(), loadBenchmark()]).then((states) => {
  const brandState = states[0];
  if (brandState.status === "rejected") {
    byId("product-subtitle").textContent = "Shared configuration error";
    byId("product-name").textContent = "—";
  }
});
