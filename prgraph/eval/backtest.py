"""Time-respecting backtest.

Replays each repo's PRs in createdAt order into a scratch graph (prgraph_eval_<repo>).
When PR P is asked about, the graph holds only PRs created before P, and only the
reviews submitted before P was created. P's own reviews are never visible when its
reviewers are predicted.

Per P:
  1. add P with only its first file -> run `impact` (leave-one-out co-change)
  2. add P's remaining files + issue links -> run `reviewers` and the three dup queries
  3. later PRs see P's reviews once their createdAt passes the review time
"""
import argparse
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from ..ingest import DATA, LOAD, to_rows
from ..parse import co_changes
from ..queries import run
from .metrics import (explicit_dup_refs, hit_at, mean, pair, prf, recall_at,
                      reciprocal_rank)

ROOT = Path(__file__).resolve().parents[2]
REPOS = ["copperheadhq/copperhead", "sqlfluff/sqlfluff"]
Q = dict(LOAD)
K = 10
DUP_QUERIES = ("dup_shared_issue", "dup_file_overlap", "dup_similar_title")


def _w(g, key, rows, repo):
    for i in range(0, len(rows), 500):
        g.query(Q[key], {"rows": rows[i:i + 500], "repo": repo})


def _group(rows):
    d = defaultdict(list)
    for r in rows:
        d[r["id"]].append(r)
    return d


def _rank_metrics(prefix, ranked, truth):
    return {f"{prefix}_h1": hit_at(ranked, truth, 1), f"{prefix}_h3": hit_at(ranked, truth, 3),
            f"{prefix}_mrr": reciprocal_rank(ranked, truth)}


def _run_cypher(g, cypher, **params):
    res = g.query(cypher, params)
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    return [dict(zip(cols, row)) for row in res.result_set]


def backtest_repo(g, raw, seed=0, variants=None):
    """variants: optional {name: cypher} of alternative reviewer queries, scored side by side
    with the production `reviewers` query (same params: $pr, $k, $max_paths)."""
    variants = variants or {}
    repo = raw["repo"]
    rows = to_rows(raw)
    bots = {p["login"] for p in rows["people"] if p["is_bot"]}
    touches, authored, fixes, refs, reviews = (
        _group(rows[k]) for k in ("touches", "authored", "fixes", "refs", "reviews"))
    prs = sorted(rows["prs"], key=lambda r: r["created"])
    author = {r["id"]: r["login"] for r in rows["authored"]}

    g.query("MERGE (r:Repo {id:$repo}) SET r.name=$repo", {"repo": repo})
    _w(g, "people", rows["people"], repo)

    rng = random.Random(seed)
    pending = []               # reviews not yet visible
    review_count = Counter()   # distinct PRs reviewed per human, visible so far
    seen_prs, cc_weights = [], {}
    out = {"reviewers": [], "impact": [], "dup_pred": defaultdict(set)}

    for p in prs:
        pid, now = p["id"], p["created"]
        vis = [r for r in pending if (r["at"] or "9") < now]
        pending = [r for r in pending if (r["at"] or "9") >= now]
        _w(g, "reviews", vis, repo)
        for key in {(r["id"], r["login"]) for r in vis if r["login"] not in bots}:
            review_count[key[1]] += 1

        _w(g, "prs", [p], repo)
        _w(g, "authored", authored.get(pid, []), repo)
        ts = touches.get(pid, [])

        # 1. impact, leave-one-out: P only shows its first file
        _w(g, "touches", ts[:1], repo)
        rest = {t["path"] for t in ts[1:]}
        if p["merged"] and rest and len(ts) <= 50:
            ranked = [r["file"] for r in run(g, "impact", pr=pid, k=K, max_paths=0)]
            out["impact"].append({"pr": pid, "n_rest": len(rest), "covered": bool(ranked),
                                  "r5": recall_at(ranked, rest, 5),
                                  "r10": recall_at(ranked, rest, 10)})
        _w(g, "touches", ts[1:], repo)
        _w(g, "fixes", fixes.get(pid, []), repo)
        _w(g, "refs", refs.get(pid, []), repo)

        # 2. reviewers
        me = author.get(pid)
        truth = {r["login"] for r in reviews.get(pid, [])} - bots - {me}
        if truth:
            ranked = [r["reviewer"] for r in run(g, "reviewers", pr=pid, k=K, max_paths=0)]
            active = [l for l, _ in review_count.most_common() if l != me]
            pool = sorted(l for l in review_count if l != me)
            rand = rng.sample(pool, len(pool))
            out["reviewers"].append({
                "pr": pid, "truth": sorted(truth), "pred": ranked[:3], "covered": bool(ranked),
                "truth_seen_before": bool(truth & set(review_count)),
                **_rank_metrics("graph", ranked, truth), **_rank_metrics("active", active, truth),
                **_rank_metrics("random", rand, truth),
                **{k: v for name, cy in variants.items() for k, v in _rank_metrics(
                    name, [r["reviewer"] for r in _run_cypher(g, cy, pr=pid, k=K, max_paths=0)],
                    truth).items()}})

        # 3. dup candidates (only earlier PRs exist in the graph)
        skip = {"dup_shared_issue": not fixes.get(pid),
                "dup_file_overlap": not ts, "dup_similar_title": not p["tokens"]}
        for name in DUP_QUERIES:
            if skip[name]:
                continue
            for r in run(g, name, pr=pid, k=K, max_paths=0):
                if r.get("kind", "PR") == "PR":
                    out["dup_pred"][name].add(pair(pid, r["other"]))

        pending += reviews.get(pid, [])
        seen_prs.append({"files": [{"path": t["path"]} for t in ts]})
        if len(ts) >= 2:  # incremental co-change: push only weights that changed
            new = {(c["a"], c["b"]): c["w"] for c in co_changes(seen_prs)}
            delta = [{"a": f"{repo}:{a}", "b": f"{repo}:{b}", "w": w}
                     for (a, b), w in new.items() if cc_weights.get((a, b)) != w]
            cc_weights = new
            _w(g, "cochange", delta, repo)
    return out, rows


def dup_truth(raw, rows):
    repo = raw["repo"]
    ids = {r["id"] for r in rows["prs"]}
    by_issue = defaultdict(set)
    for r in rows["fixes"] + rows["refs"]:
        by_issue[r["iid"]].add(r["id"])
    shared = {pair(a, b) for s in by_issue.values() for a in s for b in s if a < b}
    explicit = set()
    for p in raw["pulls"]:
        me = f"{repo}#{p['number']}"
        for n in explicit_dup_refs(p.get("body")):
            o = f"{repo}#{n}"
            if o in ids and o != me:
                explicit.add(pair(me, o))
    return shared, explicit


def summarize(repo, out, raw, rows, variants=()):
    rv = out["reviewers"]
    s = {"repo": repo, "reviewers": {
        "n": len(rv), "coverage": mean(r["covered"] for r in rv),
        "truth_reviewer_seen_before": mean(r["truth_seen_before"] for r in rv)}}
    for m in ("graph", "active", "random", *variants):
        s["reviewers"][m] = {k: mean(r[f"{m}_{k}"] for r in rv) for k in ("h1", "h3", "mrr")}
    im = out["impact"]
    s["impact"] = {"n": len(im), "coverage": mean(r["covered"] for r in im),
                   "recall@5": mean(r["r5"] for r in im), "recall@10": mean(r["r10"] for r in im),
                   "mean_files_to_find": mean(r["n_rest"] for r in im)}
    shared, explicit = dup_truth(raw, rows)
    preds = out["dup_pred"]
    s["dups"] = {"truth_shared_issue_pairs": len(shared), "truth_explicit_pairs": len(explicit),
                 "explicit_pairs": sorted(map(list, explicit))}
    variants = {n: preds[n] for n in DUP_QUERIES}
    variants["overlap_or_title"] = preds["dup_file_overlap"] | preds["dup_similar_title"]
    variants["overlap_and_title"] = preds["dup_file_overlap"] & preds["dup_similar_title"]
    variants["combined_any"] = set().union(*(preds[n] for n in DUP_QUERIES))
    for name, pred in variants.items():
        s["dups"][name] = {"vs_shared_issue": prf(pred, shared), "vs_explicit": prf(pred, explicit)}
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description="time-respecting backtest of PR Radar")
    ap.add_argument("--graph", default="prgraph_eval")
    ap.add_argument("--out", default=str(ROOT / "docs" / "eval_results.json"))
    a = ap.parse_args(argv)
    from ..db import connect, ensure_schema
    from concurrent.futures import ThreadPoolExecutor

    def one(repo):
        raw = json.loads((DATA / f"{repo.replace('/', '_')}.json").read_text(encoding="utf-8"))
        name = f"{a.graph}_{repo.split('/')[1]}"
        g = connect(name)
        try:
            g.delete()
        except Exception:
            pass
        g = connect(name)
        ensure_schema(g)
        t = time.time()
        out, rows = backtest_repo(g, raw)
        s = summarize(repo, out, raw, rows)
        s["seconds"] = round(time.time() - t, 1)
        return s

    # repos are independent graphs; run them side by side (latency-bound, not CPU)
    with ThreadPoolExecutor(len(REPOS)) as ex:
        results = list(ex.map(one, REPOS))
    print(json.dumps(results, indent=1))
    Path(a.out).write_text(json.dumps({"k": K, "repos": results}, indent=1), encoding="utf-8")
    print("wrote", a.out)
