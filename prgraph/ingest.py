"""Pull PRs/issues for a repo via `gh api graphql`, cache to data/, load into FalkorDB.

usage:
  python -m prgraph.ingest owner/repo --limit 200            # fetch (or use cache) + load
  python -m prgraph.ingest owner/repo --fetch-only           # just build the cache
  python -m prgraph.ingest owner/repo --refresh              # ignore cache
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from .parse import co_changes, extract_links, is_bot, title_tokens

DATA = Path(__file__).resolve().parent.parent / "data"

PR_QUERY = """
query($owner:String!, $name:String!, $n:Int!, $after:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(first:$n, after:$after, orderBy:{field:CREATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state createdAt mergedAt closedAt url
        author { login __typename }
        labels(first:10) { nodes { name } }
        files(first:100) { nodes { path additions deletions } }
        reviews(first:30) { nodes { author { login __typename } state submittedAt } }
        closingIssuesReferences(first:10) { nodes { number } }
      }
    }
  }
}"""

ISSUE_QUERY = """
query($owner:String!, $name:String!, $n:Int!, $after:String) {
  repository(owner:$owner, name:$name) {
    issues(first:$n, after:$after, orderBy:{field:CREATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state createdAt closedAt url
        author { login __typename }
        labels(first:10) { nodes { name } }
      }
    }
  }
}"""


def _gh_graphql(query: str, **vars) -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in vars.items():
        if v is None:
            continue
        cmd += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)["data"]


def _paginate(query: str, key: str, owner: str, name: str, limit: int, page: int) -> list[dict]:
    items, after = [], None
    while len(items) < limit:
        d = _gh_graphql(query, owner=owner, name=name, n=min(page, limit - len(items)), after=after)
        conn = d["repository"][key]
        items += conn["nodes"]
        print(f"  {key}: {len(items)}", file=sys.stderr)
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return items


def fetch(repo: str, limit: int = 200, refresh: bool = False) -> dict:
    DATA.mkdir(exist_ok=True)
    path = DATA / f"{repo.replace('/', '_')}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    owner, name = repo.split("/")
    raw = {
        "repo": repo,
        "pulls": _paginate(PR_QUERY, "pullRequests", owner, name, limit, 25),
        "issues": _paginate(ISSUE_QUERY, "issues", owner, name, limit, 50),
    }
    path.write_text(json.dumps(raw, indent=1), encoding="utf-8")
    return raw


def _login(node) -> str:
    return (node or {}).get("login") or "ghost"


def to_rows(raw: dict) -> dict:
    """Flatten raw GitHub JSON into row lists for UNWIND. Pure, testable."""
    repo = raw["repo"]
    rows = {k: [] for k in ("people", "prs", "issues", "authored", "reviews", "touches",
                             "fixes", "refs", "labels", "cochange")}
    people: dict[str, bool] = {}

    def person(node) -> str:
        login = _login(node)
        people[login] = people.get(login, False) or is_bot(login, (node or {}).get("__typename"))
        return login

    pulls = []
    for p in raw["pulls"]:
        pid = f"{repo}#{p['number']}"
        files = (p.get("files") or {}).get("nodes") or []
        pulls.append({"files": files})
        rows["prs"].append({
            "id": pid, "number": p["number"], "title": p["title"], "state": p["state"],
            "created": p["createdAt"], "merged": p.get("mergedAt"), "closed": p.get("closedAt"),
            "url": p.get("url"), "tokens": title_tokens(p["title"]),
        })
        rows["authored"].append({"login": person(p.get("author")), "id": pid, "kind": "PR"})
        for r in (p.get("reviews") or {}).get("nodes") or []:
            rows["reviews"].append({"login": person(r.get("author")), "id": pid,
                                    "state": r["state"], "at": r.get("submittedAt")})
        for f in files:
            rows["touches"].append({"id": pid, "path": f["path"], "fid": f"{repo}:{f['path']}",
                                    "add": f["additions"], "del": f["deletions"]})
        fixes, refs = extract_links(f"{p['title']}\n{p.get('body') or ''}", repo)
        fixes |= {n["number"] for n in (p.get("closingIssuesReferences") or {}).get("nodes") or []}
        refs -= fixes
        refs.discard(p["number"])
        rows["fixes"] += [{"id": pid, "iid": f"{repo}#{n}", "number": n} for n in sorted(fixes)]
        rows["refs"] += [{"id": pid, "iid": f"{repo}#{n}", "number": n} for n in sorted(refs)]
        rows["labels"] += [{"id": pid, "name": l["name"], "lid": f"{repo}:{l['name']}"}
                           for l in (p.get("labels") or {}).get("nodes") or []]
    for i in raw["issues"]:
        iid = f"{repo}#{i['number']}"
        rows["issues"].append({
            "id": iid, "number": i["number"], "title": i["title"], "state": i["state"],
            "created": i["createdAt"], "closed": i.get("closedAt"), "url": i.get("url"),
            "tokens": title_tokens(i["title"]),
        })
        rows["authored"].append({"login": person(i.get("author")), "id": iid, "kind": "Issue"})
        rows["labels"] += [{"id": iid, "name": l["name"], "lid": f"{repo}:{l['name']}"}
                           for l in (i.get("labels") or {}).get("nodes") or []]
    rows["cochange"] = [{"a": f"{repo}:{c['a']}", "b": f"{repo}:{c['b']}", "w": c["w"]}
                        for c in co_changes(pulls)]
    rows["people"] = [{"login": k, "is_bot": v} for k, v in sorted(people.items())]
    return rows


# Every write is MERGE on the id property, so re-running ingest is idempotent.
LOAD = [
    ("people", """UNWIND $rows AS r
MERGE (a:Person {login:r.login})
SET a.is_bot = r.is_bot"""),
    ("prs", """UNWIND $rows AS r
MERGE (p:PR {id:r.id})
SET p.number=r.number, p.title=r.title, p.state=r.state, p.created=r.created,
    p.merged=r.merged, p.closed=r.closed, p.url=r.url, p.tokens=r.tokens
WITH p
MATCH (repo:Repo {id:$repo}) MERGE (p)-[:IN_REPO]->(repo)"""),
    ("issues", """UNWIND $rows AS r
MERGE (i:Issue {id:r.id})
SET i.number=r.number, i.title=r.title, i.state=r.state, i.created=r.created,
    i.closed=r.closed, i.url=r.url, i.tokens=r.tokens
WITH i
MATCH (repo:Repo {id:$repo}) MERGE (i)-[:IN_REPO]->(repo)"""),
    ("authored", """UNWIND $rows AS r
MERGE (a:Person {login:r.login})
WITH a, r
OPTIONAL MATCH (p:PR {id:r.id}) WHERE r.kind = 'PR'
OPTIONAL MATCH (i:Issue {id:r.id}) WHERE r.kind = 'Issue'
WITH a, coalesce(p, i) AS t WHERE t IS NOT NULL
MERGE (a)-[:AUTHORED]->(t)"""),
    ("reviews", """UNWIND $rows AS r
MERGE (a:Person {login:r.login})
WITH a, r MATCH (p:PR {id:r.id})
MERGE (a)-[e:REVIEWED {state:r.state}]->(p)
SET e.at = r.at"""),
    ("touches", """UNWIND $rows AS r
MATCH (p:PR {id:r.id})
MERGE (f:File {id:r.fid}) SET f.path = r.path
MERGE (p)-[e:TOUCHES]->(f)
SET e.additions = r.add, e.deletions = r.del"""),
    ("fixes", """UNWIND $rows AS r
MATCH (p:PR {id:r.id})
MERGE (i:Issue {id:r.iid}) ON CREATE SET i.number = r.number
MERGE (p)-[:FIXES]->(i)"""),
    ("refs", """UNWIND $rows AS r
MATCH (p:PR {id:r.id})
MERGE (i:Issue {id:r.iid}) ON CREATE SET i.number = r.number
MERGE (p)-[:REFERENCES]->(i)"""),
    ("labels", """UNWIND $rows AS r
MERGE (l:Label {id:r.lid}) SET l.name = r.name
WITH l, r
OPTIONAL MATCH (p:PR {id:r.id})
OPTIONAL MATCH (i:Issue {id:r.id})
WITH l, coalesce(p, i) AS t WHERE t IS NOT NULL
MERGE (t)-[:HAS_LABEL]->(l)"""),
    ("cochange", """UNWIND $rows AS r
MATCH (a:File {id:r.a}), (b:File {id:r.b})
MERGE (a)-[e:CO_CHANGED]-(b)
SET e.weight = r.w"""),
]


def load(graph, raw: dict, batch: int = 500) -> dict:
    from .db import ensure_schema

    ensure_schema(graph)
    repo = raw["repo"]
    graph.query("MERGE (r:Repo {id:$repo}) SET r.name = $repo", {"repo": repo})
    rows = to_rows(raw)
    for key, q in LOAD:
        data = rows[key]
        for i in range(0, len(data), batch):
            graph.query(q, {"rows": data[i:i + batch], "repo": repo})
    return {k: len(v) for k, v in rows.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    a = ap.parse_args(argv)
    raw = fetch(a.repo, a.limit, a.refresh)
    if a.fetch_only:
        print({k: len(v) for k, v in to_rows(raw).items()})
        return
    from .db import connect
    print(load(connect(), raw))


if __name__ == "__main__":
    main()
