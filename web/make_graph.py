"""Build web/demo_graph.json: a capped overview graph per cached repo for the hero view.

    python -m web.make_graph

Nodes are people, PRs and files; edges are AUTHORED, REVIEWED and TOUCHES (bots dropped).
Clusters come from label propagation and size from PageRank, both computed here in plain
Python so demo mode needs no database. Live mode runs the FalkorDB equivalents in app.py.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from prgraph.ingest import to_rows

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
OUT = HERE / "demo_graph.json"
CAP = 400


def full_graph(rows: dict) -> tuple[dict, list]:
    """All nodes and edges from to_rows output, bots excluded."""
    bots = {p["login"] for p in rows["people"] if p["is_bot"]} | {"ghost", "github-advanced-security", "Copilot"}
    nodes: dict[str, dict] = {}
    for p in rows["people"]:
        if p["login"] not in bots:
            nodes[p["login"]] = {"id": p["login"], "label": "Person", "name": p["login"]}
    for p in rows["prs"]:
        nodes[p["id"]] = {"id": p["id"], "label": "PR", "name": f"#{p['number']} {p['title']}",
                          "state": p["state"]}
    edges = []
    for a in rows["authored"]:
        if a["kind"] == "PR" and a["login"] in nodes:
            edges.append((a["login"], a["id"], "AUTHORED"))
    seen = set()
    for r in rows["reviews"]:
        k = (r["login"], r["id"])
        if r["login"] in nodes and k not in seen:
            seen.add(k)
            edges.append((r["login"], r["id"], "REVIEWED"))
    for t in rows["touches"]:
        nodes.setdefault(t["fid"], {"id": t["fid"], "label": "File", "name": t["path"]})
        edges.append((t["id"], t["fid"], "TOUCHES"))
    return nodes, edges


def label_propagation(ids: list[str], adj: dict[str, Counter], rounds: int = 30) -> dict[str, str]:
    """Deterministic weighted LPA: ties broken by smallest label."""
    lab = {n: n for n in ids}
    for _ in range(rounds):
        changed = 0
        for n in ids:
            if not adj[n]:
                continue
            score: Counter = Counter()
            for m, w in adj[n].items():
                score[lab[m]] += w
            best = max(score.values())
            new = min(l for l, s in score.items() if s == best)
            if new != lab[n]:
                lab[n], changed = new, changed + 1
        if not changed:
            break
    return lab


def pagerank(ids: list[str], adj: dict[str, Counter], d: float = 0.85, it: int = 40) -> dict[str, float]:
    n = len(ids)
    pr = {k: 1 / n for k in ids}
    deg = {k: sum(adj[k].values()) for k in ids}
    for _ in range(it):
        nxt = {k: (1 - d) / n for k in ids}
        for k in ids:
            if deg[k]:
                share = d * pr[k] / deg[k]
                for m, w in adj[k].items():
                    nxt[m] += share * w
        pr = nxt
    return pr


def analyse(rows: dict) -> dict:
    """Full-graph analysis shared with make_demo_extra: nodes, edges, cluster + pagerank per node."""
    nodes, edges = full_graph(rows)
    adj: dict[str, Counter] = defaultdict(Counter)
    for s, t, _ in edges:
        adj[s][t] += 1
        adj[t][s] += 1
    ids = sorted(nodes)
    for k in ids:
        adj[k]  # materialise
    # Divide each neighbour's vote by its degree so a single hub maintainer cannot pull
    # the whole repo into one community.
    norm = {k: Counter({m: w / len(adj[m]) for m, w in adj[k].items()}) for k in ids}
    lab = label_propagation(ids, norm)
    pr = pagerank(ids, adj)
    return {"nodes": nodes, "edges": edges, "adj": adj, "cluster": lab, "pagerank": pr}


def overview(rows: dict, cap: int = CAP) -> dict:
    a = analyse(rows)
    nodes, adj = a["nodes"], a["adj"]
    by_deg = sorted(nodes, key=lambda k: (-len(adj[k]), k))
    people = [k for k in by_deg if nodes[k]["label"] == "Person"][:cap // 8]
    keep = people + [k for k in by_deg if nodes[k]["label"] != "Person"][:cap - len(people)]
    keep_set = set(keep)
    # Renumber clusters by size inside the kept set so colours are stable: 0 = biggest.
    sizes = Counter(a["cluster"][k] for k in keep)
    order = {c: i for i, (c, _) in enumerate(sorted(sizes.items(), key=lambda x: (-x[1], x[0])))}
    out_nodes = [{
        **nodes[k], "cluster": order[a["cluster"][k]], "degree": len(adj[k]),
        "pagerank": round(a["pagerank"][k] * len(nodes), 4),
    } for k in keep]
    out_edges = [{"source": s, "target": t, "type": ty} for s, t, ty in a["edges"]
                 if s in keep_set and t in keep_set]
    linked = {e["source"] for e in out_edges} | {e["target"] for e in out_edges}
    out_nodes = [n for n in out_nodes if n["id"] in linked]  # isolated nodes just fling outward
    return {"nodes": out_nodes, "edges": out_edges,
            "clusters": cluster_names(a, keep, order),
            "totals": {"nodes": len(nodes), "edges": len(a["edges"]),
                       "people": sum(n["label"] == "Person" for n in nodes.values()),
                       "prs": sum(n["label"] == "PR" for n in nodes.values()),
                       "files": sum(n["label"] == "File" for n in nodes.values()),
                       "clusters": len(set(a["cluster"].values()))}}


def cluster_names(a: dict, keep: list[str], order: dict) -> list[dict]:
    """Name each kept cluster by its most common top-level directory and its busiest person."""
    out = []
    for c, i in sorted(order.items(), key=lambda x: x[1]):
        members = [k for k in a["nodes"] if a["cluster"][k] == c]
        dirs = Counter(_area(a["nodes"][k]["name"]) for k in members if a["nodes"][k]["label"] == "File")
        ppl = sorted((k for k in members if a["nodes"][k]["label"] == "Person"),
                     key=lambda k: -len(a["adj"][k]))
        out.append({"id": i, "area": dirs.most_common(1)[0][0] if dirs else None,
                    "owner": ppl[0] if ppl else None, "size": len(members)})
    return out


def _area(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(root)")


def load(path: Path) -> tuple[str, dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw["repo"], to_rows(raw)


def main() -> None:
    out = {}
    for f in sorted(DATA.glob("*.json")):
        repo, rows = load(f)
        out[repo] = overview(rows)
        g = out[repo]
        print(f"{repo}: {len(g['nodes'])} nodes, {len(g['edges'])} edges (of {g['totals']})")
    OUT.write_text(json.dumps({"generated_from": "data/*.json", "repos": out}, separators=(",", ":")),
                   encoding="utf-8")


if __name__ == "__main__":
    main()
