const $ = (s) => document.querySelector(s);
const EXAMPLES = [
  "Who should review copperhead PR #243?",
  "Is PR #369 a duplicate?",
  "What breaks if src/kicad/sexp.ts changes?",
  "Why is this repo stalled?",
];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
let cy = null, last = null;

// ---- theme ---------------------------------------------------------------
function setTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { t ? localStorage.setItem("theme", t) : localStorage.removeItem("theme"); } catch {}
  if (last) drawGraph(last);
}
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
$("#theme").onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  setTheme(dark ? "light" : "dark");
};

// ---- setup -----------------------------------------------------------------
async function init() {
  for (const q of EXAMPLES) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = q;
    b.onclick = () => { $("#q").value = q; submit(); };
    $("#chips").append(b);
  }
  try {
    const r = await (await fetch("/api/repos")).json();
    $("#repo").innerHTML = r.repos.map((x) => `<option>${esc(x)}</option>`).join("");
    $("#mode").hidden = !r.demo;
  } catch (e) { $("#repo").innerHTML = "<option>copperheadhq/copperhead</option>"; }
  const qs = new URLSearchParams(location.search);
  if (qs.get("repo")) $("#repo").value = qs.get("repo");
  if (qs.get("q")) { $("#q").value = qs.get("q"); submit(); }
}
$("#form").onsubmit = (e) => { e.preventDefault(); submit(); };

async function submit() {
  const question = $("#q").value.trim();
  if (!question) return;
  $("#go").disabled = true;
  $("#answer").className = "answer muted";
  $("#answer").innerHTML = '<span class="spin"></span>Querying the graph…';
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, repo: $("#repo").value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    render(data);
  } catch (e) {
    $("#answer").className = "answer err";
    $("#answer").textContent = String(e.message || e);
  } finally { $("#go").disabled = false; }
}

// ---- rendering ---------------------------------------------------------------
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function md(s) {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function render(d) {
  last = d;
  $("#answer").className = "answer";
  $("#answer").innerHTML = md(d.answer || "(no answer)");
  const calls = d.tool_calls || [];
  $("#tcount").textContent = calls.length ? calls.length : "";
  $("#calls").className = calls.length ? "" : "muted small";
  $("#calls").innerHTML = calls.length ? calls.map((c, i) => {
    const rows = Array.isArray(c.rows) ? c.rows.length : (c.rows ?? c.row_count ?? "?");
    const cy = c.cypher || c.query || c.params?.cypher || "";
    const params = Object.fromEntries(Object.entries(c.params || {}).filter(([k]) => k !== "cypher"));
    return `<details class="call"${i === 0 ? " open" : ""}><summary><span class="name">${esc(c.tool)}</span>` +
      `<span class="rows"><b>${esc(rows)}</b> row${rows === 1 ? "" : "s"}</span></summary>` +
      `<div class="params">${esc(JSON.stringify(params))}</div>` +
      (cy ? `<pre>${esc(cy)}</pre>` : "") + `</details>`;
  }).join("") : "No graph queries were run.";
  drawGraph(d);
}

// Accepts {nodes:[{id,label,name}], edges:[{source,target,type}]} (or relationships/rels
// with start/end), or a flat [node, rel, node, ...] list.
function normPath(p) {
  const nodeOf = (n) => ({
    id: String(n.id ?? n.login ?? n.path ?? n.name),
    label: n.label ?? (n.labels && n.labels[0]) ?? "Node",
    name: n.name ?? n.login ?? n.path ?? n.title ?? n.id,
  });
  if (Array.isArray(p)) {
    const nodes = [], edges = [];
    p.forEach((x, i) => { if (i % 2 === 0) nodes.push(nodeOf(x)); });
    p.forEach((x, i) => { if (i % 2 === 1) edges.push({ source: nodes[(i - 1) / 2].id, target: nodes[(i + 1) / 2].id, type: x.type ?? x }); });
    return { nodes, edges };
  }
  const nodes = (p.nodes || []).map(nodeOf);
  const rels = p.edges || p.relationships || p.rels || [];
  const edges = rels.map((r, i) => ({
    source: String(r.source ?? r.src ?? r.start ?? r.from ?? nodes[i]?.id),
    target: String(r.target ?? r.dst ?? r.end ?? r.to ?? nodes[i + 1]?.id),
    type: r.type ?? r.relation ?? "",
  }));
  return { nodes, edges };
}

const LABEL_ORDER = ["PR", "Issue", "Person", "File", "Repo", "Label"];
function drawGraph(d) {
  const paths = (d.evidence_paths || []).map(normPath);
  const nodes = new Map(), edges = new Map();
  for (const p of paths) {
    p.nodes.forEach((n) => nodes.set(n.id, n));
    p.edges.forEach((e) => edges.set(`${e.source}|${e.type}|${e.target}`, e));
  }
  $("#gcount").textContent = nodes.size ? `${nodes.size} nodes · ${paths.length} paths` : "";
  const answer = (d.answer || "").toLowerCase();
  const focus = new Set((d.focus || []).map(String));
  if (!focus.size) for (const n of nodes.values()) {
    const short = String(n.name).split(" ")[0].toLowerCase();
    if (short.length > 2 && answer.includes(short)) focus.add(n.id);
  }
  const labels = [...new Set([...nodes.values()].map((n) => n.label))].sort((a, b) => LABEL_ORDER.indexOf(a) - LABEL_ORDER.indexOf(b));
  $("#legend").innerHTML = nodes.size ? labels.map((l) => `<span><i style="background:${shapeColor(l)}"></i>${esc(l)}</span>`).join("") +
    `<span><i style="background:${css("--accent")}"></i>on answer path</span>` : "";

  const els = [
    ...[...nodes.values()].map((n) => ({ data: { id: n.id, label: n.label, name: short(n), full: n.name, hl: focus.has(n.id) ? 1 : 0 } })),
    ...[...edges.values()].filter((e) => nodes.has(e.source) && nodes.has(e.target)).map((e) => ({
      data: { source: e.source, target: e.target, type: e.type, hl: focus.has(e.source) && focus.has(e.target) ? 1 : 0 },
    })),
  ];
  // an edge touching a focus node is on the answer path too
  els.forEach((e) => { if (e.data.source && (focus.has(e.data.source) || focus.has(e.data.target))) e.data.hl = 1; });

  if (cy) cy.destroy();
  if (!window.cytoscape) { $("#cy").innerHTML = '<p class="muted small" style="padding:12px">Graph library failed to load.</p>'; return; }
  const fg = css("--fg"), muted = css("--muted"), accent = css("--accent"), edge = css("--edge"), bg = css("--bg");
  cy = cytoscape({
    container: $("#cy"), elements: els, wheelSensitivity: 0.2,
    style: [
      { selector: "node", style: {
        "background-color": (n) => shapeColor(n.data("label")), shape: (n) => SHAPES[n.data("label")] || "ellipse",
        width: 18, height: 18, label: "data(name)", color: muted, "font-size": 10, "text-valign": "bottom",
        "text-margin-y": 4, "text-wrap": "ellipsis", "text-max-width": 140, "text-background-color": bg,
        "text-background-opacity": 0.7, "text-background-padding": 1 } },
      { selector: "node[hl = 1]", style: { "background-color": accent, width: 26, height: 26, color: fg,
        "font-weight": 600, "border-width": 3, "border-color": css("--accent-soft") } },
      { selector: "edge", style: { width: 1.2, "line-color": edge, "target-arrow-color": edge,
        "target-arrow-shape": "triangle", "arrow-scale": 0.7, "curve-style": "bezier", "font-size": 9, color: muted, "text-rotation": "autorotate", "text-background-color": bg,
        "text-background-opacity": 1, "text-background-padding": 1 } },
      { selector: "edge[hl = 1]", style: { width: 2.2, "line-color": accent, "target-arrow-color": accent } },
      { selector: ".dim", style: { opacity: 0.15 } },
      { selector: "edge.lit", style: { label: "data(type)", color: fg } },
    ],
    maxZoom: 1.4, minZoom: 0.2,
    layout: paths.every((p) => p.nodes.length <= 2)
      ? { name: "concentric", concentric: (n) => n.degree(), levelWidth: () => 1, minNodeSpacing: 40, padding: 24, animate: false }
      : { name: "breadthfirst", directed: false, roots: [...new Set(paths.map((p) => p.nodes[0]?.id).filter(Boolean))].slice(0, 3),
      spacingFactor: 1.1, padding: 24, animate: false },
  });
  cy.on("mouseover", "node", (e) => {
    const nb = e.target.closedNeighborhood();
    cy.elements().not(nb).addClass("dim"); nb.edges().addClass("lit");
    $("#ginfo").textContent = `${e.target.data("label")}: ${e.target.data("full")}`;
  });
  cy.on("mouseout", "node", () => { cy.elements().removeClass("dim lit"); $("#ginfo").textContent = HINT; });
  $("#ginfo").textContent = nodes.size ? HINT : "";
}
const HINT = "Hover a node for details and edge types. Scroll to zoom, drag to pan.";
const SHAPES = { PR: "round-rectangle", Issue: "diamond", Person: "ellipse", File: "rectangle", Repo: "hexagon", Label: "tag" };
function shapeColor(l) { return l === "Repo" ? css("--fg") : css("--node"); }
function short(n) {
  if (n.label === "PR" || n.label === "Issue") return String(n.name).split(" ")[0];
  if (n.label === "File") { const p = String(n.name).split("/"); return p.length > 2 ? "…/" + p.slice(-2).join("/") : n.name; }
  return String(n.name);
}

init();
