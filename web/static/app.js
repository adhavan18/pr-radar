/* PR Radar front end: full-bleed sigma.js graph + command bar + traced evidence paths. */
const $ = (s) => document.querySelector(s);
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Fallbacks; /api/repos sends per-repo suggestions built from real data (web/make_demo_extra.py).
const CHIPS = {
  "copperheadhq/copperhead": [
    "What if animesh-chouhan leaves copperhead?",
    "Who are the bridges?",
    "Show ownership clusters",
    "Who should review PR #243?",
    "Is PR #279 a duplicate?",
    "Why is copperhead stalled?",
  ],
  "sqlfluff/sqlfluff": [
    "What if keraion leaves sqlfluff?",
    "Who are the bridges?",
    "Show ownership clusters",
    "Who should review PR #8550?",
    "Is PR #8731 a duplicate?",
    "Why is sqlfluff stalled?",
  ],
};
const DEFAULT_CHIPS = ["Who are the bridges?", "Show ownership clusters", "Why is this repo stalled?"];
const KIND_NAMES = {
  reviewers: "Reviewer match", duplicate: "Duplicate check", impact: "Change impact", stalled: "Stall diagnosis",
  bus_factor: "Bus factor", bridges: "Bridges", clusters: "Ownership clusters",
  cross_repo: "Cross-repo people", cross_repo_expert: "Cross-repo expert",
};

const PALETTE = {
  dark: ["#7aa2f7", "#f7768e", "#9ece6a", "#e0af68", "#bb9af7", "#2ac3de", "#ff9e64", "#73daca", "#c0a8ff", "#f4a3c7"],
  light: ["#2f5bd3", "#d1335b", "#3f8f2a", "#b7791f", "#7e4fd0", "#0e8fa8", "#d9622b", "#1f9e86", "#6a4fb0", "#c2477f"],
};
const THEME = {
  dark: { rest: "#2a2f37", edge: "#1d2229", edgeDim: "#111317", dim: "#1a1d22", edgeHot: "#4a5262",
          label: "#d7dbe2", lit: "#f5c451", halo: "rgba(8,9,11,0.85)" },
  light: { rest: "#c9ced6", edge: "#dcdfe4", edgeDim: "#eeeeec", dim: "#e4e6e8", edgeHot: "#a9b0bc",
           label: "#1d2129", lit: "#c2410c", halo: "rgba(246,246,243,0.9)" },
};

let graph = null, renderer = null, overview = null, repo = null, lastAsk = null;
const S = { active: false, path: new Set(), focus: new Set(), reached: new Set(), lit: new Set(), hovered: null, timers: [] };

/* ---------------------------------------------------------------- theme */
const mode = () => {
  const t = document.documentElement.dataset.theme;
  return t || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
};
const pal = () => PALETTE[mode()];
const th = () => THEME[mode()];
// The first clusters get the full palette; the long tail reuses it washed toward the rest
// colour, so small communities still read as texture without competing with the big ones.
const clusterColor = (c) => (c == null ? th().rest : c < pal().length ? pal()[c] : mix(pal()[c % pal().length], th().rest, 0.62));
function mix(a, b, t) {
  const h = (x) => [1, 3, 5].map((i) => parseInt(x.slice(i, i + 2), 16));
  const [p, q] = [h(a), h(b)];
  return "#" + p.map((v, i) => Math.round(v + (q[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

$("#theme").onclick = () => {
  const next = mode() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch {}
  restyle();
};
matchMedia("(prefers-color-scheme: light)").addEventListener("change", restyle);

function restyle() {
  if (!graph) return;
  graph.forEachNode((n, a) => graph.setNodeAttribute(n, "color", clusterColor(a.cluster)));
  renderer.setSetting("labelColor", { color: th().label });
  renderer.setSetting("defaultEdgeColor", th().edge);
  renderLegend();
  if (lastAsk) renderCard(lastAsk.data, lastAsk.question, lastAsk.ms, false);
  renderer.refresh();
}

/* ---------------------------------------------------------------- utils */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const md = (s) => esc(s).replace(/`([^`]+)`/g, (_, c) => {
  const p = c.split("/");
  return p.length > 2 && c.length > 26 ? `<code title="${c}">…/${p.slice(-2).join("/")}</code>` : `<code>${c}</code>`;
}).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
const fmt = (n) => (n >= 10000 ? (n / 1000).toFixed(1) + "k" : n.toLocaleString());
const short = (repo) => (repo === "all" ? "All repos" : repo.split("/").pop());
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const shortName = (a) => {
  if (a.kind === "File") { const p = a.name.split("/"); return p.length > 2 ? "…/" + p.slice(-2).join("/") : a.name; }
  if ((a.kind === "PR" || a.kind === "Issue") && innerWidth <= 720) return a.name.split(" ")[0];
  if (a.kind === "PR" || a.kind === "Issue") return a.name.length > 34 ? a.name.slice(0, 33) + "…" : a.name;
  return a.name;
};

/* ---------------------------------------------------------------- sigma label drawing */
function drawLabel(ctx, data, settings) {
  if (!data.label) return;
  const size = data.labelSize || settings.labelSize, font = settings.labelFont, weight = data.labelWeight || settings.labelWeight;
  ctx.font = `${weight} ${size}px ${font}`;
  const x = data.x + data.size + 4, y = data.y + size / 3;
  ctx.lineWidth = 3.5; ctx.strokeStyle = th().halo; ctx.lineJoin = "round";
  ctx.strokeText(data.label, x, y);
  ctx.fillStyle = data.labelColor || th().label;
  ctx.fillText(data.label, x, y);
}
function drawHover(ctx, data, settings) {
  ctx.beginPath();
  ctx.arc(data.x, data.y, data.size + 3.5, 0, Math.PI * 2);
  ctx.strokeStyle = th().label; ctx.globalAlpha = 0.7; ctx.lineWidth = 1.5; ctx.stroke(); ctx.globalAlpha = 1;
  drawLabel(ctx, { ...data, label: data.label || shortName(data) }, settings);
}

/* ---------------------------------------------------------------- graph */
async function loadGraph(r) {
  const t0 = performance.now();
  const res = await fetch(`/api/graph?repo=${encodeURIComponent(r)}`);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  overview = await res.json();
  overview.clientMs = performance.now() - t0;
  build(overview);
  renderStats();
  renderLegend();
}

function build(data) {
  if (renderer) { renderer.kill(); renderer = null; }
  const rand = rng(7);
  graph = new graphology.Graph({ type: "undirected", multi: false });
  const maxPr = Math.max(...data.nodes.map((n) => n.pagerank || 0), 1e-9);
  const nc = Math.max(1, Math.min(12, data.clusters?.length || 8));
  for (const n of data.nodes) {
    const c = n.cluster < nc ? n.cluster : nc;
    const ang = c * 2.39996, rad = 1 + Math.sqrt(c) * 3;
    const base = 2.2 + 10 * Math.sqrt((n.pagerank || 0) / maxPr);
    graph.addNode(n.id, {
      x: Math.cos(ang) * rad + (rand() - 0.5) * 4, y: Math.sin(ang) * rad + (rand() - 0.5) * 4,
      size: n.label === "Person" ? Math.max(4.5, base * 1.15) : n.label === "PR" ? base * 0.85 : base * 0.9,
      label: n.name, name: n.name, kind: n.label, cluster: n.cluster, color: clusterColor(n.cluster),
    });
  }
  data.nodes.filter((n) => n.label === "Person").sort((a, b) => b.pagerank - a.pagerank).slice(0, 7)
    .forEach((n) => graph.setNodeAttribute(n.id, "star", true));
  for (const e of data.edges) {
    if (e.source !== e.target && graph.hasNode(e.source) && graph.hasNode(e.target) && !graph.hasEdge(e.source, e.target))
      graph.addEdge(e.source, e.target, { rel: e.type, size: 0.6 });
  }
  const fa2 = graphologyLibrary.layoutForceAtlas2;
  const settings = { ...fa2.inferSettings(graph), barnesHutOptimize: true, gravity: 0.35, scalingRatio: 8, strongGravityMode: true, slowDown: 4, adjustSizes: false };
  fa2.assign(graph, { iterations: REDUCED ? 500 : 120, settings });

  renderer = new Sigma(graph, $("#graph"), {
    labelFont: "Geist, ui-sans-serif, system-ui, sans-serif", labelSize: 12, labelWeight: "500",
    labelColor: { color: th().label }, labelRenderedSizeThreshold: 7, labelDensity: 0.5, labelGridCellSize: 120,
    defaultEdgeColor: th().edge, zIndex: true, stagePadding: 60, minCameraRatio: 0.05, maxCameraRatio: 3,
    labelRenderer: drawLabel, hoverRenderer: drawHover,
    defaultDrawNodeLabel: drawLabel, defaultDrawNodeHover: drawHover,
    nodeReducer, edgeReducer,
  });
  bindGraphEvents();
  // Let the layout settle on screen for a moment: the first-impression motion.
  if (!REDUCED) {
    let f = 0;
    const step = () => { fa2.assign(graph, { iterations: 4, settings }); if (++f < 70 && renderer) requestAnimationFrame(step); };
    freezeBox();
    renderer.getCamera().setState({ ratio: 1.6, x: 0.5, y: 0.5 });
    home(1500);
    requestAnimationFrame(step);
  } else {
    fa2.assign(graph, { iterations: 280, settings });
    freezeBox();
    home(0);
  }
}

function freezeBox() {
  if (!renderer || !renderer.setCustomBBox) return;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  graph.forEachNode((_, a) => { x0 = Math.min(x0, a.x); x1 = Math.max(x1, a.x); y0 = Math.min(y0, a.y); y1 = Math.max(y1, a.y); });
  renderer.setCustomBBox({ x: [x0, x1], y: [y0, y1] });
}

function nodeReducer(node, data) {
  const r = { ...data };
  const isHover = S.hovered === node;
  if (!S.active) {
    r.label = (data.kind === "Person" || data.kind === "Repo") ? data.name : null;
    if (data.star) r.forceLabel = true;
    if (S.hovered && !isHover && !graph.areNeighbors(node, S.hovered)) { r.color = th().dim; r.label = null; }
    if (S.hovered && graph.areNeighbors(node, S.hovered)) r.label = shortName(data);
  } else if (S.path.has(node)) {
    r.zIndex = 3;
    r.forceLabel = S.reached.has(node);
    r.label = S.reached.has(node) ? shortName(data) : null;
    r.size = Math.max(data.size, 5.5) * (S.reached.has(node) ? 1.25 : 1);
    if (!S.reached.has(node)) r.color = th().rest;
  } else if (S.focus.has(node)) {
    r.zIndex = 2; r.label = (data.kind === "Person" || data.kind === "Repo") ? data.name : null;
  } else {
    r.color = th().dim; r.label = null; r.zIndex = 0;
  }
  if (isHover) { r.highlighted = true; r.zIndex = 4; r.label = shortName(data); }
  return r;
}
function edgeReducer(edge, data) {
  const r = { ...data };
  if (S.active) {
    if (S.lit.has(edge)) { r.color = th().lit; r.size = 2.6; r.zIndex = 2; }
    else {
      const [a, b] = graph.extremities(edge);
      const near = S.focus.has(a) && S.focus.has(b);
      r.color = near ? th().edgeHot : th().edgeDim; r.size = near ? 0.8 : 0.5;
    }
  } else if (S.hovered) {
    const [a, b] = graph.extremities(edge);
    if (a === S.hovered || b === S.hovered) { r.color = th().edgeHot; r.size = 1.2; } else r.color = th().edgeDim;
  }
  return r;
}

function bindGraphEvents() {
  const tip = $("#tip");
  renderer.on("enterNode", ({ node }) => {
    S.hovered = node; renderer.refresh();
    const a = graph.getNodeAttributes(node);
    const kids = graph.degree(node);
    tip.innerHTML = `<b>${esc(a.name)}</b><span>${a.kind.toLowerCase()} · ${kids} links${a.cluster != null ? " · cluster " + a.cluster : ""}</span>`;
    tip.hidden = false;
    $("#graph").style.cursor = "pointer";
  });
  renderer.on("leaveNode", () => { S.hovered = null; tip.hidden = true; renderer.refresh(); $("#graph").style.cursor = ""; });
  renderer.getMouseCaptor().on("mousemovebody", (e) => {
    if (tip.hidden) return;
    const x = e.original.clientX ?? e.x, y = e.original.clientY ?? e.y;
    tip.style.transform = `translate(${Math.min(x + 14, innerWidth - 310)}px, ${y + 14}px)`;
    tip.style.left = "0"; tip.style.top = "0";
  });
  renderer.on("clickNode", ({ node }) => {
    const a = graph.getNodeAttributes(node);
    let q = null;
    if (repo === "all") q = a.kind === "Person" || a.kind === "Repo" ? "Who works across repos?" : null;
    else if (a.kind === "Person") q = `What if ${a.name} leaves ${short(repo)}?`;
    else if (a.kind === "PR") q = `Who should review PR ${a.name.split(" ")[0]}?`;
    else if (a.kind === "File") q = `What breaks if ${a.name} changes?`;
    if (q) { $("#q").value = q; ask(); }
  });
  renderer.on("clickStage", () => { if (S.active) closeCard(); });
}

/* ---------------------------------------------------------------- stats + legend */
function renderStats() {
  const t = overview.totals || {};
  const ms = lastAsk ? lastAsk.ms : overview.ms;
  const cells = [
    [fmt(t.nodes ?? overview.nodes.length), "nodes"],
    [fmt(t.edges ?? overview.edges.length), "edges"],
    [fmt(t.people ?? 0), "people", "hide-sm"],
    [fmt(t.prs ?? 0), "PRs", "hide-sm"],
    [String(document.querySelectorAll('#repos button:not([data-repo="all"])').length || 1), "repos"],
    [`${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`, lastAsk ? "last query" : "graph query", "live"],
  ];
  $("#stats").innerHTML = cells.map(([v, l, c]) => `<div class="stat ${c || ""}"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");
}
function renderLegend() {
  const cl = (overview?.clusters || []).filter((c) => c.area).slice(0, 6);
  if (!cl.length) { $("#legend").hidden = true; return; }
  $("#legend").hidden = false;
  $("#legend").innerHTML = `<div class="t">Ownership clusters · size = PageRank</div>` +
    cl.map((c) => `<span style="--c:${clusterColor(c.id)}"><i></i>${esc(c.area)}${c.owner ? ` · ${esc(c.owner)}` : ""}</span>`).join("");
}

/* ---------------------------------------------------------------- repos + chips */
async function init() {
  let repos = ["copperheadhq/copperhead"], demo = false;
  try { const r = await (await fetch("/api/repos")).json(); repos = r.repos.length ? r.repos : repos; demo = r.demo; Object.assign(CHIPS, r.suggestions || {}); } catch {}
  $("#mode").hidden = !demo;
  const qs = new URLSearchParams(location.search);
  const start = repos.includes(qs.get("repo")) ? qs.get("repo") : repos[0];
  $("#repos").innerHTML = repos.map((r) => `<button type="button" role="tab" data-repo="${esc(r)}">${esc(short(r))}</button>`).join("");
  $("#repos").querySelectorAll("button").forEach((b) => (b.onclick = () => selectRepo(b.dataset.repo)));
  await selectRepo(start);
  if (qs.get("q")) { $("#q").value = qs.get("q"); ask(); }
}

async function selectRepo(r) {
  if (r === repo) return;
  repo = r;
  $("#repos").querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.repo === r)));
  closeCard(true);
  lastAsk = null;
  renderChips();
  try { await loadGraph(r); } catch (e) { $("#stats").innerHTML = `<div class="stat"><b class="err">graph unavailable</b><span>${esc(e.message)}</span></div>`; }
}

function renderChips() {
  const list = CHIPS[repo] || DEFAULT_CHIPS;
  $("#chips").innerHTML = list.map((q, i) =>
    `<button type="button" role="listitem" class="chip" style="animation-delay:${120 + i * 45}ms">${i < 3 ? '<span class="dot"></span>' : ""}${esc(q)}</button>`).join("");
  $("#chips").querySelectorAll(".chip").forEach((b, i) => (b.onclick = () => { $("#q").value = list[i]; ask(); }));
}

/* ---------------------------------------------------------------- asking */
$("#form").onsubmit = (e) => { e.preventDefault(); ask(); };
addEventListener("keydown", (e) => {
  if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && document.activeElement !== $("#q"))) {
    e.preventDefault(); $("#q").focus(); $("#q").select();
  } else if (e.key === "Escape" && !$("#eval").open) {
    if (document.activeElement === $("#q")) $("#q").blur(); else closeCard();
  }
});

async function ask() {
  const question = $("#q").value.trim();
  if (!question || !repo) return;
  document.body.classList.add("asked");
  $("#chips").querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(b.textContent === question)));
  $("#go").disabled = true; $("#go").classList.add("busy");
  openCard();
  $("#kind").textContent = "Querying the graph";
  $("#qtitle").textContent = question;
  $("#answer").className = "answer loading";
  $("#answer").innerHTML = '<div class="skeleton" style="width:92%"></div><div class="skeleton" style="width:78%"></div><div class="skeleton" style="width:85%"></div>';
  $("#paths").innerHTML = ""; $("#calls").innerHTML = ""; $("#pcount").textContent = ""; $("#tcount").textContent = "";
  const t0 = performance.now();
  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, repo }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    lastAsk = { data, question, ms: performance.now() - t0 };
    renderCard(data, question, lastAsk.ms, true);
    renderStats();
  } catch (e) {
    $("#kind").textContent = "Error";
    $("#answer").className = "answer err";
    $("#answer").textContent = String(e.message || e);
  } finally { $("#go").disabled = false; $("#go").classList.remove("busy"); }
}

function openCard() {
  const c = $("#card");
  c.hidden = false;
  requestAnimationFrame(() => c.classList.add("open"));
}
function closeCard(silent) {
  const c = $("#card");
  c.classList.remove("open");
  setTimeout(() => { if (!c.classList.contains("open")) c.hidden = true; }, 320);
  clearTrace();
  if (!silent) {
    document.body.classList.remove("asked");
    $("#chips").querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", "false"));
    lastAsk = null;
    if (overview) renderStats();
    if (renderer) home(700);
  }
}
$("#close").onclick = () => closeCard();

function renderCard(d, question, ms, animate) {
  $("#kind").textContent = (KIND_NAMES[d.kind] || "Answer") + (ms != null ? ` · ${Math.round(ms)} ms` : "");
  $("#qtitle").textContent = question;
  let text = d.answer || "(no answer)", note = "";
  const m = text.match(/^\(Demo: ([^\n]+)\)\s*/);
  if (m) { note = `<span class="note">Demo mode · ${esc(m[1])}</span>`; text = text.slice(m[0].length); }
  $("#answer").className = "answer";
  $("#answer").innerHTML = note + md(text);

  const paths = (d.evidence_paths || []).filter((p) => p.nodes?.length);
  $("#pcount").textContent = paths.length || "";
  $("#paths").innerHTML = paths.length ? paths.map((p, i) => pathHtml(p, i)).join("") : '<div class="answer">No paths returned.</div>';
  $("#paths").querySelectorAll(".pathrow").forEach((el) => (el.onclick = () => trace(d, [Number(el.dataset.i)])));

  const calls = d.tool_calls || [];
  $("#tcount").textContent = calls.length || "";
  $("#calls").innerHTML = calls.map(callHtml).join("");
  if (animate) trace(d, null);
}

function nodeColor(n) {
  if (graph?.hasNode(n.id)) return clusterColor(graph.getNodeAttribute(n.id, "cluster"));
  return th().label;
}
function pathHtml(p, i) {
  const byId = Object.fromEntries(p.nodes.map((n) => [n.id, n]));
  const parts = [];
  p.nodes.forEach((n, k) => {
    if (k > 0) {
      const prev = p.nodes[k - 1];
      const e = p.edges.find((e) => (e.source === prev.id && e.target === n.id) || (e.target === prev.id && e.source === n.id));
      if (e) parts.push(`<span class="rel ${e.source === n.id ? "back" : ""}">${esc(e.type)}</span>`);
      else parts.push('<span class="rel">·</span>');
    }
    const name = n.label === "File" ? shortName({ kind: "File", name: n.name || n.id }) : (n.name || n.id);
    parts.push(`<span class="node ${esc(n.label)}" style="--c:${nodeColor(n)}" title="${esc(n.name || n.id)}"><i></i><span>${esc(name)}</span></span>`);
  });
  void byId;
  return `<button type="button" class="pathrow" data-i="${i}" style="animation-delay:${i * 60}ms">${parts.join("")}</button>`;
}
function callHtml(c) {
  const rows = Array.isArray(c.rows) ? c.rows : [];
  const cy = c.cypher || c.query || "";
  const params = Object.fromEntries(Object.entries(c.params || {}).filter(([k]) => k !== "cypher"));
  const cols = rows.length ? Object.keys(rows[0]).filter((k) => k !== "evidence").slice(0, 4) : [];
  const cell = (v) => esc(typeof v === "object" ? JSON.stringify(v) : v);
  const kw = (s) => esc(s).replace(/\b(MATCH|OPTIONAL|WHERE|WITH|RETURN|ORDER BY|LIMIT|CALL|YIELD|UNWIND|AND|OR|NOT|DISTINCT|DESC|ASC|AS|CASE|WHEN|THEN|ELSE|END)\b/g, '<span class="kw">$1</span>');
  return `<div class="call">
    <div class="call-head"><b>${esc(c.tool)}</b><span>${esc(JSON.stringify(params))}</span><span>${rows.length || c.row_count || 0} rows</span></div>
    ${cy ? `<pre>${kw(cy)}</pre>` : ""}
    ${cols.length ? `<table><thead><tr>${cols.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${rows.slice(0, 5).map((r) => `<tr>${cols.map((k) => `<td>${cell(r[k])}</td>`).join("")}</tr>`).join("")}</tbody></table>` : ""}
  </div>`;
}

/* ---------------------------------------------------------------- trace animation */
function clearTrace() {
  S.timers.forEach(clearTimeout); S.timers = [];
  S.active = false; S.path.clear(); S.focus.clear(); S.reached.clear(); S.lit.clear();
  document.querySelectorAll(".pathrow.on").forEach((el) => el.classList.remove("on"));
  renderer?.refresh();
}

function ensureNode(n, near) {
  if (graph.hasNode(n.id)) return;
  const kind = n.label || (n.id.includes(":") ? "File" : n.id.includes("#") ? "PR" : "Person");
  let x = 0, y = 0;
  const anchor = near.find((id) => graph.hasNode(id));
  if (anchor) { const a = graph.getNodeAttributes(anchor); x = a.x; y = a.y; }
  const r = rng(n.id.length * 31 + n.id.charCodeAt(0));
  const ang = r() * Math.PI * 2, dist = 18 + r() * 22;
  graph.addNode(n.id, { x: x + Math.cos(ang) * dist, y: y + Math.sin(ang) * dist, size: 4, name: n.name || n.id, label: n.name || n.id, kind, cluster: null, color: th().label, extra: true });
}

function trace(d, only) {
  clearTrace();
  if (!graph) return;
  const paths = (d.evidence_paths || []).filter((p) => p.nodes?.length);
  const chosen = only ? only.map((i) => paths[i]).filter(Boolean) : paths.slice(0, 8);
  const hops = [];
  chosen.forEach((p, pi) => {
    p.nodes.forEach((n, k) => ensureNode(n, [p.nodes[k - 1]?.id, p.nodes[k + 1]?.id, ...p.nodes.map((x) => x.id)].filter(Boolean)));
    p.edges.forEach((e) => { if (e.source !== e.target && !graph.hasEdge(e.source, e.target)) graph.addEdge(e.source, e.target, { rel: e.type, size: 0.6, extra: true }); });
    p.nodes.forEach((n) => S.path.add(n.id));
    const seq = [{ node: p.nodes[0].id }];
    for (let k = 1; k < p.nodes.length; k++) {
      const a = p.nodes[k - 1].id, b = p.nodes[k].id;
      seq.push({ node: b, edge: graph.hasEdge(a, b) ? graph.edge(a, b) : null });
    }
    hops.push({ pi: only ? only[pi] : pi, seq });
  });
  for (const id of d.focus || []) if (graph.hasNode(id)) S.focus.add(id);
  S.path.forEach((id) => S.focus.add(id));
  S.active = true;
  renderer.refresh();
  flyTo([...S.path].concat((d.focus || []).slice(0, 80)).filter((id) => graph.hasNode(id)));

  const HOP = REDUCED ? 0 : 240, GAP = REDUCED ? 0 : 300, START = REDUCED ? 0 : 520;
  hops.forEach((h, i) => {
    const row = document.querySelector(`.pathrow[data-i="${h.pi}"]`);
    h.seq.forEach((s, k) => {
      const at = START + i * GAP + k * HOP;
      const fire = () => {
        S.reached.add(s.node);
        if (s.edge) S.lit.add(s.edge);
        if (k === 0) row?.classList.add("on");
        renderer.refresh();
      };
      if (at === 0) fire(); else S.timers.push(setTimeout(fire, at));
    });
  });
}

function home(duration) {
  const W = innerWidth, H = innerHeight, phone = W <= 720;
  const free = phone ? { l: -30, r: W + 30, t: 240, b: H - 70 } : { l: 0, r: W, t: 330, b: H - 30 };
  const ids = []; graph.forEachNode((n) => ids.push(n));
  flyTo(ids, free, 1.04, duration);
}

function flyTo(ids, freeArea, pad = 1.35, duration) {
  if (!ids.length) return;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const id of ids) {
    const p = renderer.getNodeDisplayData(id);
    if (!p) continue;
    x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y);
  }
  if (!isFinite(x0)) return;
  const W = innerWidth, H = innerHeight;
  const phone = W <= 720;
  // Visible region not covered by chrome: card on the right (desktop) or bottom (phone), command bar on top.
  const free = freeArea || (phone ? { l: 0, r: W, t: 130, b: H * 0.5 } : { l: 0, r: W - 470, t: 170, b: H - 20 });
  const fw = (free.r - free.l) / Math.min(W, H), fh = (free.b - free.t) / Math.min(W, H);
  const ratio = Math.min(1.1, Math.max(0.1, Math.max((x1 - x0) / fw, (y1 - y0) / fh) * pad + 0.04));
  // Offset the centre so the bbox sits in the middle of the free region.
  const offX = ((free.l + free.r) / 2 - W / 2) / Math.min(W, H) * ratio;
  const offY = ((free.t + free.b) / 2 - H / 2) / Math.min(W, H) * ratio;
  renderer.getCamera().animate(
    { x: (x0 + x1) / 2 - offX, y: (y0 + y1) / 2 + offY, ratio },
    { duration: REDUCED ? 0 : duration ?? 900, easing: "cubicInOut" },
  );
}

/* ---------------------------------------------------------------- eval sheet */
$("#evalbtn").onclick = async () => {
  const dlg = $("#eval");
  dlg.showModal();
  if (dlg.dataset.loaded) return;
  try {
    const r = await fetch("/api/eval");
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    $("#evalbody").innerHTML = evalHtml(await r.json());
    dlg.dataset.loaded = "1";
  } catch (e) { $("#evalbody").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
};
$("#evalclose").onclick = () => $("#eval").close();
$("#eval").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const bar = (label, v, main, max = 1) =>
  `<div class="brow ${main ? "main" : ""}"><span>${esc(label)}</span><div class="track"><div class="fill" style="--v:${Math.max(0, Math.min(1, (v || 0) / max))}"></div></div><b>${pct(v)}</b></div>`;

function evalHtml(d) {
  const before = Object.fromEntries((d.before?.repos || []).map((r) => [r.repo, r]));
  return (d.repos || []).map((r) => {
    const rv = r.reviewers || {}, im = r.impact || {}, dp = r.dups || {};
    const b = before[r.repo];
    const methods = Object.entries(dp).filter(([k, v]) => k.startsWith("dup_") || k.includes("_and_") || k.includes("_or_") || k === "combined_any")
      .filter(([k, v]) => k !== "dup_shared_issue" && v?.vs_shared_issue)
      .map(([k, v]) => [k.replace(/^dup_/, "").replace(/_/g, " "), v.vs_shared_issue]);
    const bestDup = methods.sort((a, b) => b[1].f1 - a[1].f1).slice(0, 4);
    const graphH3 = rv.graph?.h3, actH3 = rv.active?.h3;
    return `<section class="erepo">
      <h3>${esc(r.repo)} · ${rv.n ?? "?"} reviewer cases · ${im.n ?? "?"} impact cases</h3>
      <div class="metrics">
        <div class="metric"><h4>Reviewer match</h4><p>True reviewer in the top 3 suggestions (hit@3)${b?.reviewers ? `, was ${pct(b.reviewers.graph?.h3)}` : ""}.</p>
          <div class="bars">${bar("graph", graphH3, true)}${bar("most active", actH3)}${bar("random", rv.random?.h3)}</div></div>
        <div class="metric"><h4>Change impact</h4><p>Files later co-changed that the graph predicted. Coverage ${pct(im.coverage)}.</p>
          <div class="bars">${bar("recall@5", im["recall@5"], true)}${bar("recall@10", im["recall@10"], true)}</div></div>
        <div class="metric"><h4>Duplicate PRs</h4><p>F1 against PR pairs that close the same issue (${dp.truth_shared_issue_pairs ?? "?"} pairs).</p>
          <div class="bars">${bestDup.map(([k, v], i) => bar(k, v.f1, i === 0)).join("")}</div></div>
      </div>
      ${graphH3 != null && actH3 != null && graphH3 < actH3 ? `<p class="honest" style="margin-top:10px">Honest note: on reviewer match the plain "most active reviewer" baseline still beats graph-only scoring here (${pct(actH3)} vs ${pct(graphH3)}). We report it rather than hide it.</p>` : ""}
    </section>`;
  }).join("");
}

init();
