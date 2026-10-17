"""Offline (no-DB) versions of the cross-repo queries, for demo data and the "All repos" graph.

Mirrors prgraph.queries `shared_contributors` and `cross_repo_expert` over data/*.json, so demo
mode shows the same answers the live graph gives. Person nodes are shared by login across repos;
that is what makes these questions answerable at all.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from prgraph.parse import is_bot
from prgraph.queries import QUERIES

# Same generic-word list as the Cypher in queries.cross_repo_expert.
STOP = set(re.search(r"NOT t IN \[([^\]]*)\]", QUERIES["cross_repo_expert"]).group(1)
           .replace("'", "").split(","))
MIN_HITS = 2  # a reviewed PR must share >= 2 topic words (one generic word is noise)
SKIP = {"ghost", "github-advanced-security", "Copilot"}


def path_tokens(path: str) -> list[str]:
    return re.split(r"/", re.sub(r"[._\- ]", "/", path.lower()))


def topic(tokens: list[str], paths) -> set[str]:
    return {t for t in list(tokens or []) + [x for p in paths for x in path_tokens(p)]
            if len(t) >= 3 and t not in STOP}


class Cross:
    """Index over every cached repo's to_rows output."""

    def __init__(self, repos: dict[str, dict]):
        self.repos = repos
        self.pr = {}                              # pr id -> row
        self.repo_of = {}                         # pr id -> repo
        self.author = {}                          # pr id -> login
        self.files = defaultdict(list)            # pr id -> paths
        self.reviewed = defaultdict(set)          # login -> pr ids reviewed
        self.active = defaultdict(lambda: defaultdict(set))  # login -> repo -> pr ids
        self.bot = {}
        for repo, rows in repos.items():
            for p in rows["people"]:
                self.bot[p["login"]] = p["is_bot"] or p["login"] in SKIP or is_bot(p["login"])
            for p in rows["prs"]:
                self.pr[p["id"]], self.repo_of[p["id"]] = p, repo
            for t in rows["touches"]:
                self.files[t["id"]].append(t["path"])
            for a in rows["authored"]:
                if a["kind"] == "PR":
                    self.author[a["id"]] = a["login"]
                    self.active[a["login"]][repo].add(a["id"])
            for r in rows["reviews"]:
                self.reviewed[r["login"]].add(r["id"])
                self.active[r["login"]][repo].add(r["id"])

    def human(self, login) -> bool:
        return bool(login) and not self.bot.get(login, True)

    def shared_contributors(self, k: int = 10) -> list[dict]:
        out = []
        for login, per in self.active.items():
            if not self.human(login) or len(per) < 2:
                continue
            ranked = sorted(per.items(), key=lambda x: (-len(x[1]), x[0]))
            ev = []
            for repo, prs in ranked[:3]:
                p = sorted(prs)[0]
                rel = "AUTHORED" if self.author.get(p) == login else "REVIEWED"
                ev.append(_path(("Person", login, login), rel,
                                ("PR", p, f"#{self.pr[p]['number']} {self.pr[p]['title']}"), "IN_REPO",
                                ("Repo", repo, repo)))
            out.append({"person": login, "n_repos": len(per),
                        "repos": [f"{r} ({len(p)})" for r, p in ranked],
                        "prs": sum(len(p) for p in per.values()), "evidence": ev})
        out.sort(key=lambda r: (-r["n_repos"], -r["prs"], r["person"]))
        return out[:k]

    def cross_repo_expert(self, pid: str, k: int = 10) -> list[dict]:
        home, me = self.repo_of[pid], self.author.get(pid)
        mine = topic(self.pr[pid]["tokens"], self.files[pid])
        if not mine:
            return []
        out = []
        for login, prs in self.reviewed.items():
            if not self.human(login) or login == me:
                continue
            hits = {}
            for q in prs:
                if self.repo_of[q] == home:
                    continue
                h = mine & topic(self.pr[q]["tokens"], self.files[q])
                if len(h) >= MIN_HITS:
                    hits[q] = h
            if not hits:
                continue
            best = max(sorted(hits), key=lambda q: len(hits[q]))
            matched = set().union(*hits.values())
            here = len(self.active[login].get(home, ()))
            out.append({"person": login, "repos": sorted({self.repo_of[q] for q in hits}),
                        "matching_reviews": len(hits), "topics": sorted(matched)[:8], "prs_here": here,
                        "score": round(len(matched) + math.log(1 + len(hits)), 4),
                        "evidence": [_path(("Person", login, login), "REVIEWED",
                                           ("PR", best, f"#{self.pr[best]['number']} {self.pr[best]['title']}"),
                                           "IN_REPO", ("Repo", self.repo_of[best], self.repo_of[best]))]})
        out.sort(key=lambda r: (-r["score"], r["person"]))
        return out[:k]

    def bridge_graph(self, per_repo: int = 25) -> dict:
        """'All repos' overview: Repo hubs, each repo's most active people, and the people
        active in 2+ repos (the cross-repo bridges, own colour)."""
        names = sorted(self.repos)
        idx = {r: i for i, r in enumerate(names)}
        shared = {l for l, per in self.active.items() if self.human(l) and len(per) >= 2}
        nodes = {r: {"id": r, "label": "Repo", "name": r, "cluster": idx[r], "degree": 0, "pagerank": 6.0}
                 for r in names}
        edges = []
        keep = set(shared)
        for r in names:
            ppl = sorted((l for l, per in self.active.items() if self.human(l) and r in per),
                         key=lambda l: (-len(self.active[l][r]), l))
            keep |= set(ppl[:per_repo])
        for l in sorted(keep):
            per = self.active[l]
            tot = sum(len(p) for p in per.values())
            home = max(per, key=lambda r: (len(per[r]), r))
            nodes[l] = {"id": l, "label": "Person", "name": l, "degree": len(per),
                        "cluster": len(names) if l in shared else idx[home],
                        "pagerank": round(1 + 2 * len(per) + math.log(1 + tot), 3)}
            for r, prs in per.items():
                if r in nodes:
                    edges.append({"source": l, "target": r, "type": "ACTIVE_IN", "weight": len(prs)})
                    nodes[r]["degree"] += 1
        clusters = [{"id": idx[r], "area": r.split("/")[-1], "owner": None, "size": 0} for r in names]
        clusters.append({"id": len(names), "area": f"bridges: active in 2+ repos ({len(shared)})", "owner": None,
                         "size": len(shared)})
        return {"nodes": list(nodes.values()), "edges": edges, "clusters": clusters,
                "totals": {"nodes": len(nodes), "edges": len(edges), "repos": len(names),
                           "people": len(keep), "bridges": len(shared)}}


def _path(*parts):
    nodes = [{"label": p[0], "id": p[1], "name": p[2][:60]} for p in parts[0::2]]
    edges = [{"source": nodes[i]["id"], "target": nodes[i + 1]["id"], "type": t}
             for i, t in enumerate(parts[1::2])]
    return {"nodes": nodes, "edges": edges}
