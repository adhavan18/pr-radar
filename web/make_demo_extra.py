"""Canned demo answers for the graph-algorithm questions (bus factor, bridges, clusters).

    python -m web.make_demo_extra

Everything is computed from the cached data/*.json with the same clustering the hero graph
uses (web.make_graph), and written to web/demo_extra.json. app.py merges it with
demo_responses.json.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from prgraph.algos import ALGOS, _LPA
from web.make_graph import DATA, analyse, load, overview

HERE = Path(__file__).resolve().parent
OUT = HERE / "demo_extra.json"
SHORT = {"copperheadhq/copperhead": "copperhead", "sqlfluff/sqlfluff": "sqlfluff"}

BRIDGES_CYPHER = _LPA + """
WITH node, communityId WHERE node:File AND node.id STARTS WITH ($repo + ':')
MATCH path = (h:Person)-[:AUTHORED|REVIEWED]->(:PR)-[:TOUCHES]->(node)
WHERE NOT coalesce(h.is_bot, false)
WITH h, count(DISTINCT communityId) AS clusters, collect(path) AS paths
RETURN h.login AS person, clusters, paths[0..$max_paths] AS evidence
ORDER BY clusters DESC LIMIT $k"""


def _node(a, k):
    n = a["nodes"][k]
    return {"id": k, "label": n["label"], "name": n["name"][:60]}


def _path(a, *hops):
    """hops: node ids in order; edge types inferred from labels."""
    nodes = [_node(a, k) for k in hops]
    edges = []
    for x, y in zip(hops, hops[1:]):
        lx = a["nodes"][x]["label"]
        if lx == "Person":
            edges.append({"source": x, "target": y, "type": a["rel"].get((x, y), "AUTHORED")})
        else:
            edges.append({"source": x, "target": y, "type": "TOUCHES"})
    return {"nodes": nodes, "edges": edges}


def build(repo: str, rows: dict) -> list[dict]:
    a = analyse(rows)
    g = overview(rows)
    nodes, cl = a["nodes"], a["cluster"]
    rel = {}
    for s, t, ty in a["edges"]:
        if nodes[s]["label"] == "Person":
            rel[(s, t)] = "REVIEWED" if ty == "REVIEWED" and (s, t) not in rel else rel.get((s, t), ty)
    a["rel"] = rel
    # human -> PRs, PR -> files
    prs_of: dict[str, set] = defaultdict(set)
    files_of: dict[str, set] = defaultdict(set)
    for s, t, ty in a["edges"]:
        if ty == "TOUCHES":
            files_of[s].add(t)
        else:
            prs_of[s].add(t)
    humans_of_file: dict[str, set] = defaultdict(set)
    prs_of_file: dict[str, set] = defaultdict(set)
    for h, prs in prs_of.items():
        for p in prs:
            for f in files_of[p]:
                humans_of_file[f].add(h)
                prs_of_file[f].add(p)
    short = SHORT.get(repo, repo.split("/")[-1])
    out = []

    # ---- bus factor of the busiest human ---------------------------------------------
    person = max(prs_of, key=lambda h: (len(prs_of[h]), h))
    solo = sorted((f for f, hs in humans_of_file.items() if hs == {person}),
                  key=lambda f: (-len(prs_of_file[f]), f))
    touched = {f for p in prs_of[person] for f in files_of[p]}
    dirs = Counter(nodes[f]["name"].split("/")[0] for f in solo)
    ev = []
    used: set = set()
    for f in solo:
        if len(ev) >= 6:
            break
        fresh = sorted(prs_of_file[f] - used)
        if not fresh and len(solo) > 12:
            continue  # prefer evidence that spreads over different PRs
        p = (fresh or sorted(prs_of_file[f]))[0]
        used.add(p)
        ev.append(_path(a, person, p, f))
    top = ", ".join(f"`{nodes[f]['name']}`" for f in solo[:3])
    out.append({
        "kind": "bus_factor",
        "question": f"What if {person} leaves {short}?",
        "answer": (f"**{len(solo)} of {len(touched)} files** {person} has touched would have **no other human** "
                   f"who authored or reviewed a PR on them ({round(100 * len(solo) / max(1, len(touched)))}%). "
                   f"{person} is on {len(prs_of[person])} of {len(rows['prs'])} cached PRs. "
                   f"Most exposed: {top}. By area: "
                   + ", ".join(f"{d} ({n})" for d, n in dirs.most_common(4)) + "."),
        "tool_calls": [{"tool": "bus_factor", "params": {"repo": repo, "person": person, "k": 10},
                        "cypher": ALGOS["bus_factor"].strip(),
                        "rows": [{"file": nodes[f]["name"], "prs": len(prs_of_file[f]), "remaining_humans": 0}
                                 for f in solo[:10]]}],
        "evidence_paths": ev,
        "focus": [person] + solo[:30],
    })

    # ---- bridges: people whose files span the most ownership clusters ------------------
    big = {c for c, n in Counter(cl[f] for f in files_of_all(files_of)).items() if n >= 3}
    span = {}
    for h, prs in prs_of.items():
        fs = {f for p in prs for f in files_of[p] if cl[f] in big}
        span[h] = fs
    ranked = sorted(prs_of, key=lambda h: (-len({cl[f] for f in span[h]}), -len(prs_of[h]), h))[:5]
    ev, rws = [], []
    for h in ranked[:3]:
        seen = set()
        for f in sorted(span[h], key=lambda f: -len(prs_of_file[f])):
            if cl[f] in seen or len(seen) >= 2:
                continue
            seen.add(cl[f])
            p = next(p for p in sorted(prs_of[h]) if f in files_of[p])
            ev.append(_path(a, h, p, f))
    for h in ranked:
        rws.append({"person": h, "clusters": len({cl[f] for f in span[h]}), "prs": len(prs_of[h])})
    nbig = len(big)
    out.append({
        "kind": "bridges",
        "question": "Who are the bridges?",
        "answer": ("Bridges are people whose PRs and reviews cross ownership clusters, so losing them cuts "
                   "communication between parts of the code. " +
                   "; ".join(f"**{r['person']}** spans {r['clusters']} of {nbig} clusters ({r['prs']} PRs)"
                             for r in rws[:4]) + "."),
        "tool_calls": [{"tool": "bridges", "params": {"repo": repo, "k": 5}, "cypher": BRIDGES_CYPHER.strip(),
                        "rows": rws}],
        "evidence_paths": ev,
        "focus": ranked,
    })

    # ---- ownership clusters ------------------------------------------------------------
    rws, ev, focus = [], [], []
    for c in g["clusters"][:6]:
        members = [k for k, n in g_nodes(g).items() if n["cluster"] == c["id"]]
        fs = [k for k in members if nodes[k]["label"] == "File"]
        owners = Counter(h for f in fs for h in humans_of_file[f])
        top_people = [h for h, _ in owners.most_common(3)]
        rws.append({"cluster": c["id"], "area": c["area"], "files": len(fs), "people": top_people})
        focus += members
        if fs and top_people:
            f = max(fs, key=lambda f: len(prs_of_file[f]))
            h = max(humans_of_file[f], key=lambda h: owners[h])
            p = next(p for p in sorted(prs_of[h]) if f in files_of[p])
            ev.append(_path(a, h, p, f))
    out.append({
        "kind": "clusters",
        "question": "Show ownership clusters",
        "answer": (f"Label propagation over people, PRs and files finds **{len(g['clusters'])} communities** "
                   f"in the {len(g['nodes'])}-node overview. The largest: " +
                   "; ".join(f"**{r['area']}** ({r['files']} files, "
                             f"{', '.join(r['people'][:2]) or 'no humans'})" for r in rws[:4]) +
                   ". Colours on the graph are these clusters."),
        "tool_calls": [{"tool": "ownership_clusters", "params": {"repo": repo, "min_files": 3},
                        "cypher": ALGOS["ownership_clusters"].strip(), "rows": rws}],
        "evidence_paths": ev,
        "focus": focus,
    })
    return out


def files_of_all(files_of):
    return {f for fs in files_of.values() for f in fs}


def g_nodes(g):
    return {n["id"]: n for n in g["nodes"]}


def main() -> None:
    out = {}
    for f in sorted(DATA.glob("*.json")):
        repo, rows = load(f)
        out[repo] = build(repo, rows)
        for x in out[repo]:
            print(f"{repo} | {x['question']}\n  {x['answer'][:220]}")
    OUT.write_text(json.dumps({"generated_from": "data/*.json", "repos": out}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
