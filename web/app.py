"""PR Radar web UI.

    uvicorn web.app:app --reload            # live, needs FalkorDB + prgraph.agent
    PRGRAPH_DEMO=1 uvicorn web.app:app      # canned answers from web/demo_responses.json
"""
from __future__ import annotations

import importlib
import json
import math
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
DEMO_FILE = HERE / "demo_responses.json"
EXTRA_FILE = HERE / "demo_extra.json"
GRAPH_FILE = HERE / "demo_graph.json"
EVAL_FILE = HERE.parent / "docs" / "eval_results.json"

app = FastAPI(title="PR Radar")


class Ask(BaseModel):
    question: str
    repo: str


def demo_mode() -> bool:
    return os.environ.get("PRGRAPH_DEMO", "") not in ("", "0", "false")


def _demo() -> dict:
    repos = json.loads(DEMO_FILE.read_text(encoding="utf-8"))["repos"]
    if EXTRA_FILE.exists():
        for repo, items in json.loads(EXTRA_FILE.read_text(encoding="utf-8"))["repos"].items():
            have = {x["question"] for x in repos.get(repo, [])}
            repos.setdefault(repo, []).extend(x for x in items if x["question"] not in have)
    return repos


def _kind(q: str) -> str | None:
    q = q.lower()
    if "leave" in q or "bus factor" in q or "left" in q:
        return "bus_factor"
    if re.search(r"(other|another|different) repo", q):
        return "cross_repo_expert"
    if re.search(r"across repos|cross[- ]repo|shared (contributor|people)|(2|two|multiple) repos", q):
        return "cross_repo"
    if "bridge" in q:
        return "bridges"
    if "cluster" in q or "ownership" in q or "communit" in q:
        return "clusters"
    if "review" in q:
        return "reviewers"
    if "dup" in q or "already fixed" in q:
        return "duplicate"
    if "break" in q or "impact" in q or "risk" in q:
        return "impact"
    if "stall" in q or "stuck" in q or "slow" in q:
        return "stalled"
    return None


def _specifics(q: str) -> set[str]:
    """PR numbers, file paths and logins-like tokens that pin a question to one entity."""
    return set(re.findall(r"#\d+|[\w.-]+/[\w./-]+|[\w-]+\.\w{1,4}", q.lower()))


def _covers(question: str, hit: dict) -> bool:
    """A canned answer of the right kind may stand in for a rephrased question only when it is
    about the same entity (same PR number / path, or the same person for bus factor)."""
    if hit["kind"] == "bus_factor":
        return hit["focus"][0].lower() in question.lower()
    return _specifics(question) <= _specifics(hit["question"])


def demo_answer(question: str, repo: str) -> dict:
    repos = _demo()
    if repo not in repos:
        raise HTTPException(404, f"no demo data for {repo}")
    items = repos[repo]
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()
    hit = next((x for x in items if norm(x["question"]) == norm(question)), None)
    if hit is None:
        kind = _kind(question)
        same = [x for x in items if x["kind"] == kind]
        hit = next((x for x in same if _specifics(question) and _specifics(question) <= _specifics(x["question"])),
                   same[0] if same else None)
        if hit is not None and _covers(question, hit):
            return {**hit, "question": question, "demo": True}
        if hit is None:
            return {"answer": "Demo mode answers the example question types: reviewers, duplicates, "
                              "impact, stalled, bus factor, bridges, ownership clusters and cross-repo experts.",
                    "tool_calls": [], "evidence_paths": [], "demo": True}
        hit = {**hit, "answer": f"(Demo: closest canned question is \"{hit['question']}\")\n\n" + hit["answer"]}
    return {**hit, "demo": True}


@app.get("/api/repos")
def repos():
    if demo_mode():
        names = [r for r in _demo() if r != ALL]
    else:
        names = []
        for f in sorted(DATA.glob("*.json")):
            try:
                with f.open(encoding="utf-8") as fh:
                    head = fh.read(400)
                m = re.search(r'"repo"\s*:\s*"([^"]+)"', head)
                names.append(m.group(1) if m else f.stem.replace("_", "/", 1))
            except OSError:
                continue
    if len(names) >= 2:
        names.append(ALL)
    sugg = {}
    if EXTRA_FILE.exists():
        try:
            sugg = json.loads(EXTRA_FILE.read_text(encoding="utf-8")).get("suggestions", {})
        except ValueError:
            pass
    return {"repos": names, "demo": demo_mode(), "suggestions": sugg}


@app.post("/api/ask")
def ask(body: Ask):
    if not body.question.strip():
        raise HTTPException(400, "question is empty")
    if demo_mode():
        return demo_answer(body.question, body.repo)
    try:
        agent = importlib.import_module("prgraph.agent")
        fn = getattr(agent, "ask")
    except (ImportError, AttributeError) as e:
        raise HTTPException(503, f"agent unavailable ({e}). Set PRGRAPH_DEMO=1 to use canned answers.")
    try:
        return fn(body.question, body.repo)
    except Exception as e:  # surface DB/LLM failures to the UI instead of a bare 500
        raise HTTPException(502, f"agent error: {type(e).__name__}: {e}")


LIVE_GRAPH = [
    ("nodes", """CALL algo.labelPropagation({nodeLabels:['Person','PR','File'],
  relationshipTypes:['AUTHORED','REVIEWED','TOUCHES']}) YIELD node, communityId
WITH node, communityId
WHERE (node:PR AND (node)-[:IN_REPO]->(:Repo {id:$repo}))
   OR (node:File AND node.id STARTS WITH ($repo + ':'))
   OR (node:Person AND NOT coalesce(node.is_bot, false)
       AND (node)-[:AUTHORED|REVIEWED]->(:PR)-[:IN_REPO]->(:Repo {id:$repo}))
OPTIONAL MATCH (node)-[e]-()
WITH node, communityId, count(e) AS degree
ORDER BY degree DESC LIMIT $cap
RETURN coalesce(node.id, node.login) AS id, labels(node)[0] AS label,
       coalesce(node.path, node.login, '#' + toString(node.number) + ' ' + node.title) AS name,
       node.state AS state, communityId AS cluster, degree"""),
    ("pagerank", """CALL algo.pageRank(null, null) YIELD node, score
WITH node, score WHERE coalesce(node.id, node.login) IN $ids
RETURN coalesce(node.id, node.login) AS id, score"""),
    ("edges", """MATCH (a)-[r:AUTHORED|REVIEWED|TOUCHES]->(b)
WHERE coalesce(a.id, a.login) IN $ids AND coalesce(b.id, b.login) IN $ids
RETURN DISTINCT coalesce(a.id, a.login) AS source, coalesce(b.id, b.login) AS target, type(r) AS type"""),
]


ALL = "all"  # pseudo-repo: every ingested repo, joined through shared Person nodes

# "All repos" view: Repo hubs, each repo's most active humans, and everyone active in 2+ repos.
LIVE_ALL = """MATCH (a:Person)-[:AUTHORED|REVIEWED]->(p:PR)-[:IN_REPO]->(r:Repo)
WHERE NOT coalesce(a.is_bot, false) AND a.login <> 'ghost'
WITH a, r, count(DISTINCT p) AS prs
RETURN a.login AS person, r.id AS repo, prs"""


def live_all() -> dict:
    db = importlib.import_module("prgraph.db")
    rows = _rows(db.connect().query(LIVE_ALL))
    per: dict = {}
    for r in rows:
        per.setdefault(r["person"], {})[r["repo"]] = r["prs"]
    names = sorted({r["repo"] for r in rows})
    idx = {r: i for i, r in enumerate(names)}
    shared = {p for p, d in per.items() if len(d) >= 2}
    keep = set(shared)
    for r in names:
        keep |= set(sorted((p for p in per if r in per[p]), key=lambda p: -per[p][r])[:25])
    nodes = {r: {"id": r, "label": "Repo", "name": r, "cluster": idx[r], "degree": 0, "pagerank": 6.0} for r in names}
    edges = []
    for p in sorted(keep):
        d = per[p]
        nodes[p] = {"id": p, "label": "Person", "name": p, "degree": len(d),
                    "cluster": len(names) if p in shared else idx[max(d, key=d.get)],
                    "pagerank": round(1 + 2 * len(d) + math.log(1 + sum(d.values())), 3)}
        for r, n in d.items():
            edges.append({"source": p, "target": r, "type": "ACTIVE_IN", "weight": n})
            nodes[r]["degree"] += 1
    clusters = [{"id": idx[r], "area": r.split("/")[-1], "owner": None} for r in names]
    clusters.append({"id": len(names), "area": f"bridges: active in 2+ repos ({len(shared)})", "owner": None})
    return {"nodes": list(nodes.values()), "edges": edges, "clusters": clusters,
            "totals": {"nodes": len(nodes), "edges": len(edges), "repos": len(names), "bridges": len(shared)}}


def _rows(res) -> list[dict]:
    cols = [c[1] if isinstance(c, (list, tuple)) else c for c in res.header]
    return [dict(zip(cols, r)) for r in res.result_set]


def live_graph(repo: str, cap: int = 400) -> dict:
    db = importlib.import_module("prgraph.db")
    g = db.connect()
    nodes = _rows(g.query(LIVE_GRAPH[0][1], {"repo": repo, "cap": cap}))
    ids = [n["id"] for n in nodes]
    pr = {r["id"]: r["score"] for r in _rows(g.query(LIVE_GRAPH[1][1], {"ids": ids}))}
    edges = _rows(g.query(LIVE_GRAPH[2][1], {"ids": ids}))
    sizes: dict = {}
    for n in nodes:
        sizes[n["cluster"]] = sizes.get(n["cluster"], 0) + 1
    order = {c: i for i, c in enumerate(sorted(sizes, key=lambda c: -sizes[c]))}
    for n in nodes:
        n["cluster"] = order[n["cluster"]]
        n["pagerank"] = round(pr.get(n["id"], 0) * max(1, len(nodes)), 4)
    return {"nodes": nodes, "edges": edges, "clusters": [{"id": i} for i in range(len(order))],
            "totals": {"nodes": len(nodes), "edges": len(edges)}}


@app.get("/api/graph")
def graph(repo: str):
    t0 = time.perf_counter()
    if demo_mode():
        repos = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))["repos"]
        if repo not in repos:
            raise HTTPException(404, f"no demo graph for {repo}")
        out = repos[repo]
    else:
        try:
            out = live_all() if repo == ALL else live_graph(repo)
        except Exception as e:
            raise HTTPException(502, f"graph error: {type(e).__name__}: {e}")
    return {**out, "repo": repo, "demo": demo_mode(), "ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.get("/api/eval")
def eval_results():
    if not EVAL_FILE.exists():
        raise HTTPException(404, "no eval results yet (run the backtest)")
    return json.loads(EVAL_FILE.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")
