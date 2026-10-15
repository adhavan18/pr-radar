"""Build web/demo_responses.json from the cached GitHub snapshots in data/.

Each canned answer is computed by evaluating the same logic as the Cypher in
prgraph/queries.py, in plain Python over prgraph.ingest.to_rows(), so the demo
shows real evidence from the snapshot without a running FalkorDB.

    python -m web.make_demo
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from prgraph.ingest import to_rows
from prgraph.parse import is_bot as _is_bot
from prgraph.queries import DEFAULTS, QUERIES

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "demo_responses.json"

# question template per kind; {pr}/{file} are filled per repo
KINDS = {
    "reviewers": "Who should review {short} PR #{pr}?",
    "duplicate": "Is PR #{pr} a duplicate?",
    "impact": "What breaks if {file} changes?",
    "stalled": "Why is this repo stalled?",
}
# chosen targets per repo (must exist in the snapshot); others picked automatically
TARGETS = {
    "copperheadhq/copperhead": {"reviewers": 243, "duplicate": 279, "impact": "src/kicad/sexp.ts"},
}


def is_bot(login):
    """Same rule ingest uses for Person.is_bot (prgraph.parse.BOT_LOGINS + [bot] suffix)."""
    return not login or _is_bot(login)


def n(label, id_, name=None):
    return {"id": id_, "label": label, "name": name or id_}


def path(*parts):
    """parts: node, rel_type, node, rel_type, node ... ; rel_type '<X' means reversed."""
    nodes = [p for p in parts[0::2]]
    edges = []
    for i, t in enumerate(parts[1::2]):
        a, b = nodes[i]["id"], nodes[i + 1]["id"]
        if t.startswith("<"):
            t, a, b = t[1:], b, a
        edges.append({"source": a, "target": b, "type": t})
    return {"nodes": nodes, "edges": edges}


class Snapshot:
    def __init__(self, raw):
        self.repo = raw["repo"]
        r = self.rows = to_rows(raw)
        self.prs = {p["id"]: p for p in r["prs"]}
        self.issues = {i["id"]: i for i in r["issues"]}
        self.author = {a["id"]: a["login"] for a in r["authored"]}
        self.reviews = defaultdict(set)  # pr -> logins
        for v in r["reviews"]:
            self.reviews[v["id"]].add(v["login"])
        self.files = defaultdict(set)  # pr -> paths
        self.touchers = defaultdict(set)  # path -> prs
        for t in r["touches"]:
            self.files[t["id"]].add(t["path"])
            self.touchers[t["path"]].add(t["id"])
        self.links = defaultdict(set)  # pr -> (issue, type)
        for kind, key in (("FIXES", "fixes"), ("REFERENCES", "refs")):
            for x in r[key]:
                self.links[x["id"]].add((x["iid"], kind))
        pre = self.repo + ":"
        self.cochange = defaultdict(dict)
        for c in r["cochange"]:
            a, b = c["a"][len(pre):], c["b"][len(pre):]
            self.cochange[a][b] = c["w"]
            self.cochange[b][a] = c["w"]

    def pid(self, num):
        return f"{self.repo}#{num}"

    def prn(self, pid):
        p = self.prs.get(pid)
        return n("PR", pid, f"#{pid.split('#')[1]}" + (f" {p['title'][:40]}" if p else ""))

    def filen(self, p):
        return n("File", f"{self.repo}:{p}", p)

    def person(self, login):
        return n("Person", login, login)

    # ---- query emulations, each returns rows like the Cypher would --------
    def q_reviewers(self, pid):
        me = self.author.get(pid)
        cand = defaultdict(lambda: {"files": set(), "prs": set(), "weight": 0, "paths": []})
        for f in sorted(self.files[pid]):
            for o in sorted(self.touchers[f]):
                if o == pid:
                    continue
                rels = [(self.author.get(o), "AUTHORED")] + [(l, "REVIEWED") for l in sorted(self.reviews[o])]
                for login, rt in rels:
                    if not login or login == me or is_bot(login):
                        continue
                    c = cand[login]
                    c["files"].add(f)
                    c["prs"].add(o)
                    c["weight"] += 2 if rt == "REVIEWED" else 1
                    c["paths"].append(path(self.prn(pid), "TOUCHES", self.filen(f), "<TOUCHES",
                                           self.prn(o), "<" + rt, self.person(login)))
        rows = [{"reviewer": k, "files": len(v["files"]), "prs": len(v["prs"]),
                 "score": len(v["files"]) * 3 + v["weight"], "evidence": v["paths"][:DEFAULTS["max_paths"]]}
                for k, v in cand.items()]
        return sorted(rows, key=lambda x: -x["score"])[:DEFAULTS["k"]]

    def q_dup_shared_issue(self, pid):
        rows = []
        for iid, _ in sorted(self.links[pid]):
            for o, ls in self.links.items():
                if o != pid and any(i == iid for i, _ in ls):
                    t1 = next(t for i, t in self.links[pid] if i == iid)
                    t2 = next(t for i, t in ls if i == iid)
                    rows.append({"other": o, "state": self.prs[o]["state"], "title": self.prs[o]["title"],
                                 "via": iid, "evidence": path(self.prn(pid), t1, n("Issue", iid, "#" + iid.split("#")[1]),
                                                               "<" + t2, self.prn(o))})
        return rows

    def q_dup_file_overlap(self, pid):
        mine = len(self.files[pid])
        if not mine:
            return []
        rows = []
        for o in self.prs:
            if o == pid:
                continue
            shared = sorted(self.files[pid] & self.files[o])
            if shared and len(shared) / mine >= DEFAULTS["min_overlap"]:
                rows.append({"other": o, "state": self.prs[o]["state"], "title": self.prs[o]["title"],
                             "shared": len(shared), "mine": mine,
                             "evidence": [path(self.prn(pid), "TOUCHES", self.filen(f), "<TOUCHES", self.prn(o))
                                          for f in shared[:DEFAULTS["max_paths"]]]})
        return sorted(rows, key=lambda x: -x["shared"])[:DEFAULTS["k"]]

    def q_dup_similar_title(self, pid, t=DEFAULTS["min_jaccard"]):
        mine = set(self.prs[pid]["tokens"])
        rows = []
        if not mine:
            return rows
        for kind, coll in (("PR", self.prs), ("Issue", self.issues)):
            for o, x in coll.items():
                other = set(x["tokens"])
                if o == pid or not (len(mine) * t <= len(other) and len(other) * t <= len(mine)):
                    continue
                common = len(mine & other)
                jac = common / len(mine | other) if common else 0.0
                if common and jac >= t:
                    on = self.prn(o) if kind == "PR" else n("Issue", o, "#" + o.split("#")[1])
                    rows.append({"other": o, "kind": kind, "state": x["state"], "title": x["title"],
                                 "common": common, "jaccard": round(jac, 3),
                                 "evidence": path(self.prn(pid), "IN_REPO", n("Repo", self.repo), "<IN_REPO", on)})
        return sorted(rows, key=lambda x: (-x["jaccard"], -x["common"]))[:DEFAULTS["k"]]

    def q_impact_file(self, f):
        rows = []
        for g, w in self.cochange[f].items():
            both = sorted(pr for pr in self.touchers[f] & self.touchers[g])
            ev = [path(self.filen(f), "<TOUCHES", self.prn(pr), "TOUCHES", self.filen(g))
                  for pr in both[:DEFAULTS["max_paths"]]]
            rows.append({"file": g, "strength": w, "evidence": ev})
        return sorted(rows, key=lambda x: (-x["strength"], x["file"]))[:DEFAULTS["k"]]

    def q_stale_open_prs(self):
        rows = [{"pr": p["id"], "title": p["title"], "created": p["created"], "author": self.author.get(p["id"]),
                 "reviewers": len(self.reviews[p["id"]]),
                 "evidence": path(self.person(self.author.get(p["id"], "ghost")), "AUTHORED", self.prn(p["id"]),
                                  "IN_REPO", n("Repo", self.repo))}
                for p in self.prs.values() if p["state"] == "OPEN"]
        return sorted(rows, key=lambda x: x["created"])[:DEFAULTS["k"]]

    def q_last_merges(self):
        rows = [{"pr": p["id"], "merged": p["merged"], "author": self.author.get(p["id"]),
                 "evidence": path(self.person(self.author.get(p["id"], "ghost")), "AUTHORED", self.prn(p["id"]),
                                  "IN_REPO", n("Repo", self.repo))}
                for p in self.prs.values() if p["merged"]]
        return sorted(rows, key=lambda x: x["merged"], reverse=True)[:DEFAULTS["k"]]

    def q_reviewer_load(self):
        by = defaultdict(set)
        for pr, ls in self.reviews.items():
            for l in ls:
                if not is_bot(l):
                    by[l].add(pr)
        rows = [{"reviewer": l, "reviewed": len(ps),
                 "open_reviewed": sum(1 for p in ps if self.prs.get(p, {}).get("state") == "OPEN"),
                 "evidence": [path(self.person(l), "REVIEWED", self.prn(p), "IN_REPO", n("Repo", self.repo))
                              for p in sorted(ps)[:DEFAULTS["max_paths"]]]}
                for l, ps in by.items()]
        return sorted(rows, key=lambda x: -x["reviewed"])[:DEFAULTS["k"]]


def call(tool, params, rows):
    return {"tool": tool, "params": params, "cypher": QUERIES[tool].strip(), "rows": rows}


def flat_paths(rows, limit):
    out = []
    for r in rows[:limit]:
        ev = r.get("evidence")
        out += ev if isinstance(ev, list) else [ev]
    return out


def strip(rows):
    return [{k: v for k, v in r.items() if k != "evidence"} for r in rows]


def days(iso, now):
    return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days


def build(s: Snapshot):
    tg = TARGETS.get(s.repo, {})
    open_prs = [p for p in s.prs.values() if p["state"] == "OPEN"]
    busiest = max(open_prs or s.prs.values(), key=lambda p: len(s.files[p["id"]]))
    rev_pr = tg.get("reviewers", busiest["number"])
    dup_pr = tg.get("duplicate") or max(s.prs.values(), key=lambda p: len(s.q_dup_file_overlap(p["id"])))["number"]
    hot = tg.get("impact") or max(s.cochange, key=lambda f: len(s.cochange[f]))
    short = s.repo.split("/")[1]
    now = max(datetime.fromisoformat(p["created"].replace("Z", "+00:00")) for p in s.prs.values())
    out = []

    # reviewers
    pid = s.pid(rev_pr)
    rows = s.q_reviewers(pid)
    top = rows[:3]
    ans = (f"For PR #{rev_pr} (\"{s.prs[pid]['title']}\", by {s.author.get(pid)}), the strongest reviewer candidates are "
           + ", ".join(f"**{r['reviewer']}** (score {r['score']}: {r['files']} shared files across {r['prs']} PRs)" for r in top)
           + ". Scores weight shared files x3, reviews x2 and authorship x1; the PR author and bots are excluded."
           if top else f"No one else has touched the files in PR #{rev_pr}.")
    out.append({"kind": "reviewers", "question": KINDS["reviewers"].format(short=short, pr=rev_pr),
                "answer": ans, "tool_calls": [call("reviewers", {"pr": pid, "k": 10, "max_paths": 3}, strip(rows))],
                "evidence_paths": flat_paths(rows, 3), "focus": [pid] + [r["reviewer"] for r in top]})

    # duplicate
    pid = s.pid(dup_pr)
    a, b, c = s.q_dup_shared_issue(pid), s.q_dup_file_overlap(pid), s.q_dup_similar_title(pid)
    parts = []
    if a:
        parts.append("it links the same issue as " + ", ".join(
            f"#{r['other'].split('#')[1]} ({r['state'].lower()}, via #{r['via'].split('#')[1]})" for r in a[:3]))
    if b:
        parts.append("it shares most of its files with " + ", ".join(
            f"#{r['other'].split('#')[1]} ({r['shared']}/{r['mine']} files, {r['state'].lower()})" for r in b[:3]))
    if c:
        parts.append("its title is close to " + ", ".join(
            f"{r['kind']} #{r['other'].split('#')[1]} (Jaccard {r['jaccard']:.2f})" for r in c[:2]))
    verdict = "Likely overlaps existing work" if (a or len(parts) >= 2) else ("Possibly related work" if parts else "No duplicate found")
    ans = f"{verdict} for PR #{dup_pr} (\"{s.prs[pid]['title']}\")" + (": " + "; ".join(parts) + "." if parts else ".")
    out.append({"kind": "duplicate", "question": KINDS["duplicate"].format(pr=dup_pr), "answer": ans,
                "tool_calls": [call("dup_shared_issue", {"pr": pid}, strip(a)),
                               call("dup_file_overlap", {"pr": pid, "min_overlap": DEFAULTS["min_overlap"], "k": 10, "max_paths": 3}, strip(b)),
                               call("dup_similar_title", {"pr": pid, "min_jaccard": DEFAULTS["min_jaccard"], "k": 10}, strip(c))],
                "evidence_paths": [r["evidence"] for r in a[:4]] + flat_paths(b, 2) + [r["evidence"] for r in c[:2]],
                "focus": [pid] + [r["other"] for r in (a + b)[:3]]})

    # impact (impact_file query)
    rows = s.q_impact_file(hot)
    ans = (f"`{hot}` has been changed together with {len(s.cochange[hot])} other files. The most coupled are "
           + ", ".join(f"`{r['file']}` ({r['strength']} PRs)" for r in rows[:4])
           + ". Review those when this file changes." if rows else f"`{hot}` has no co-change history in the snapshot.")
    out.append({"kind": "impact", "question": KINDS["impact"].format(file=hot), "answer": ans,
                "tool_calls": [call("impact_file", {"file": f"{s.repo}:{hot}", "k": 10, "max_paths": 3}, strip(rows))],
                "evidence_paths": flat_paths(rows, 4), "focus": [f"{s.repo}:{hot}"] + [f"{s.repo}:{r['file']}" for r in rows[:4]]})

    # stalled
    st, lm, rl = s.q_stale_open_prs(), s.q_last_merges(), s.q_reviewer_load()
    n_open = len(open_prs)
    unreviewed = sum(1 for p in open_prs if not any(not is_bot(l) for l in s.reviews[p["id"]]))
    last = lm[0] if lm else None
    humans = [r for r in rl if not is_bot(r["reviewer"])]
    bits = [f"{n_open} PRs are open and {unreviewed} of them have no human review"]
    if st:
        bits.append(f"the oldest, #{st[0]['pr'].split('#')[1]} by {st[0]['author']}, has waited {days(st[0]['created'], now)} days")
    if last:
        bits.append(f"the last merge was #{last['pr'].split('#')[1]} on {last['merged'][:10]} ({days(last['merged'], now)} days before the newest PR)")
    if humans:
        tot = sum(r["reviewed"] for r in humans)
        bits.append(f"review load is concentrated: {humans[0]['reviewer']} did {humans[0]['reviewed']} of {tot} human reviews in the top {len(humans)}")
    ans = "Signals from the graph: " + "; ".join(bits) + "."
    out.append({"kind": "stalled", "question": KINDS["stalled"], "answer": ans,
                "tool_calls": [call("stale_open_prs", {"repo": s.repo, "k": 10}, strip(st)),
                               call("last_merges", {"repo": s.repo, "k": 10}, strip(lm)),
                               call("reviewer_load", {"repo": s.repo, "k": 10, "max_paths": 3}, strip(rl))],
                "evidence_paths": flat_paths(st, 4) + flat_paths(lm, 2) + flat_paths(humans, 1),
                "focus": [s.repo] + [r["pr"] for r in st[:4]]})
    return out


def main():
    data = {}
    for f in sorted((ROOT / "data").glob("*.json")):
        s = Snapshot(json.loads(f.read_text(encoding="utf-8")))
        data[s.repo] = build(s)
    OUT.write_text(json.dumps({"generated_from": "data/*.json", "repos": data}, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({', '.join(f'{k}: {len(v)}' for k, v in data.items())})")


if __name__ == "__main__":
    main()
