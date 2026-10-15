"""Smoke test against a live FalkorDB: ingest cached repos, run every query/algo once.

    python scripts/smoke.py

Reads data/*.json only (no GitHub calls). Prints row counts and errors; exit 1 on any error.
"""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prgraph.agent.memory import MEMORY  # noqa: E402
from prgraph.algos import ALGOS  # noqa: E402
from prgraph.db import connect  # noqa: E402
from prgraph.ingest import DATA, fetch, load  # noqa: E402
from prgraph.queries import DEFAULTS, QUERIES, param_names  # noqa: E402

REPOS = ["copperheadhq/copperhead", "sqlfluff/sqlfluff"]


def sample(graph, repo):
    """Pick real ids from the graph for each repo."""
    def one(q):
        r = graph.query(q, {"repo": repo}).result_set
        return r[0][0] if r else None
    pr = one("MATCH (p:PR)-[:IN_REPO]->(:Repo {id:$repo}), (p)-[:TOUCHES]->(:File) "
             "RETURN p.id ORDER BY p.number DESC LIMIT 1")
    issue = one("MATCH (:PR)-[:FIXES]->(i:Issue)-[:IN_REPO]->(:Repo {id:$repo}) RETURN i.id LIMIT 1") \
        or one("MATCH (i:Issue)-[:IN_REPO]->(:Repo {id:$repo}) RETURN i.id LIMIT 1")
    f = one("MATCH (:PR)-[:IN_REPO]->(:Repo {id:$repo}), (f:File)-[:CO_CHANGED]-() "
            "WHERE f.id STARTS WITH $repo RETURN f.id LIMIT 1")
    person = one("MATCH (a:Person)-[:AUTHORED]->(:PR)-[:IN_REPO]->(:Repo {id:$repo}) RETURN a.login LIMIT 1")
    return {"repo": repo, "pr": pr, "issue": issue, "file": f, "a": pr, "b": person,
            "session": "smoke", "qid": f"smoke-{repo}-{time.time()}", "question": "smoke",
            "answer": "smoke", "evidence": [], "about": [pr] if pr else [], "at": int(time.time()),
            "ref": pr}


def main():
    g = connect()
    errors = 0
    for repo in REPOS:
        if not (DATA / f"{repo.replace('/', '_')}.json").exists():
            print(f"skip {repo}: no cache"); continue
        t = time.time()
        print(f"ingest {repo}: {load(g, fetch(repo))} ({time.time() - t:.1f}s)")
    for repo in REPOS:
        params = sample(g, repo)
        print(f"\n== {repo}  pr={params['pr']} issue={params['issue']} file={params['file']}")
        for group in (QUERIES, ALGOS, MEMORY):
            for name, q in group.items():
                need = param_names(q)
                p = {k: v for k, v in {**DEFAULTS, **params}.items() if k in need}
                if any(p.get(k) is None for k in need):
                    print(f"  {name:20s} SKIP (no sample for {sorted(k for k in need if p.get(k) is None)})")
                    continue
                t = time.time()
                try:
                    n = len(g.query(q, p).result_set)
                    print(f"  {name:20s} {n:4d} rows  {1000 * (time.time() - t):6.0f} ms")
                except Exception as e:
                    errors += 1
                    print(f"  {name:20s} ERROR {e}")
                    traceback.print_exc(limit=1)
    print(f"\n{errors} error(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
