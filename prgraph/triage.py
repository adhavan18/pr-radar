"""PR Radar acts: perceive a PR, decide from the graph, act, remember.

    python -m prgraph.triage --repo adhavan18/pr-radar-demo --pr 2          # dry run, prints comment
    python -m prgraph.triage --repo adhavan18/pr-radar-demo --pr 2 --post   # comment + labels

Steps: fetch the PR with `gh api graphql` (same node shape as ingest), MERGE it into the graph
with ingest's row builders, run the duplicate / reviewer / impact / bus-factor queries, compose
one markdown comment, store a (:Triage)-[:ON]->(:PR) memory node, and optionally post.
Posting is hard-limited to POST_ALLOWED.
"""
import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
import uuid

from .agent.tools import call_tool, to_jsonable
from .ingest import LOAD, to_rows

POST_ALLOWED = {"adhavan18/pr-radar-demo"}
# History copied from another repo is renumbered by this offset so it can't collide with the
# demo repo's own PR/issue numbers (see remap_raw). Rendered as `<name>#<original>`.
HISTORY_OFFSET = 1000
HISTORY_NAME = {"adhavan18/pr-radar-demo": "copperhead"}

PR_ONE = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      number title body state createdAt mergedAt closedAt url
      author { login __typename }
      labels(first:10) { nodes { name } }
      files(first:100) { nodes { path additions deletions } }
      reviews(first:30) { nodes { author { login __typename } state submittedAt } }
      closingIssuesReferences(first:10) { nodes { number title body state createdAt closedAt url
                                                  author { login __typename } } }
    }
  }
}"""

TRIAGE_QUERIES: dict[str, str] = {
    # bus factor restricted to this PR's files: files whose earlier PRs had exactly one human
    "pr_bus_factor": """
MATCH (p:PR {id:$pr})-[:TOUCHES]->(f:File)
MATCH (h:Person)-[:AUTHORED|REVIEWED]->(q:PR)-[:TOUCHES]->(f)
WHERE q <> p AND NOT coalesce(h.is_bot, false)
WITH f, collect(DISTINCT h) AS humans, count(DISTINCT q) AS prs
WHERE size(humans) = 1 AND prs >= $min_prs
WITH f, humans[0] AS owner, prs
MATCH path = (owner)-[:AUTHORED|REVIEWED]->(:PR)-[:TOUCHES]->(f)
WITH f, owner, prs, collect(path) AS paths
RETURN f.path AS file, owner.login AS only_human, prs, paths[0..$max_paths] AS evidence
ORDER BY prs DESC, file LIMIT $k""",

    "prior_triage": """
MATCH path = (t:Triage)-[:ON]->(o:PR)
WHERE o.id IN $related AND o.id <> $pr
RETURN o.id AS pr, t.at AS at, t.verdict AS verdict, path AS evidence
ORDER BY t.at ASC""",

    "remember_triage": """
MATCH (p:PR {id:$pr})
CREATE (t:Triage {id:$tid, at:$at, verdict:$verdict, findings:$findings, labels:$labels})
CREATE (t)-[:ON]->(p)
WITH t
OPTIONAL MATCH (o:PR) WHERE o.id IN $related
WITH t, collect(o) AS rel
FOREACH (o IN rel | MERGE (t)-[:FLAGGED]->(o))
RETURN t.id AS triage, size(rel) AS flagged""",
}


# ---------------------------------------------------------------- perceive
def _gh(*args: str, input: str | None = None) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                         input=input, check=True)
    return out.stdout


def fetch_pr(repo: str, number: int) -> dict:
    owner, name = repo.split("/")
    out = _gh("api", "graphql", "-f", f"query={PR_ONE}", "-f", f"owner={owner}",
              "-f", f"name={name}", "-F", f"number={number}")
    return json.loads(out)["data"]["repository"]["pullRequest"]


def ingest_pr(graph, repo: str, pr: dict) -> None:
    """MERGE one PR (+ the issues it closes) with ingest's row builders. Idempotent.

    cochange/collab rows are dropped: they are aggregate weights over the whole history and
    recomputing them from a single PR would overwrite the real counts.
    """
    from .db import ensure_schema

    issues = [{**i, "labels": {"nodes": []}}
              for i in (pr.get("closingIssuesReferences") or {}).get("nodes") or [] if i.get("title")]
    rows = to_rows({"repo": repo, "pulls": [pr], "issues": issues})
    ensure_schema(graph)
    graph.query("MERGE (r:Repo {id:$repo}) SET r.name = $repo", {"repo": repo})
    for key, q in LOAD:
        if key in ("cochange", "collab") or not rows[key]:
            continue
        graph.query(q, {"rows": rows[key], "repo": repo})


# ---------------------------------------------------------------- decide
def _run(graph, name: str, params: dict, repo: str) -> list[dict]:
    if name in TRIAGE_QUERIES:
        from .queries import DEFAULTS, param_names
        q = TRIAGE_QUERIES[name]
        p = {k: v for k, v in {**DEFAULTS, "min_prs": 2, **params}.items() if k in param_names(q)}
        res = graph.query(q, p)
        cols = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
        return [to_jsonable(dict(zip(cols, r))) for r in res.result_set]
    return call_tool(graph, name, params, repo=repo)


def gather(graph, repo: str, number: int) -> dict:
    pid = f"{repo}#{number}"
    f = {name: _run(graph, name, {"pr": pid, "k": 5}, repo)
         for name in ("dup_shared_issue", "dup_file_overlap", "dup_similar_title",
                      "reviewers", "impact", "pr_bus_factor")}
    seen = set()
    f["reviewers"] = [r for r in f["reviewers"] if not (r["reviewer"] in seen or seen.add(r["reviewer"]))][:3]
    # one shared file is not a duplicate signal; only keep real overlaps
    f["dup_file_overlap"] = [r for r in f["dup_file_overlap"] if r["shared"] >= 2][:3]
    f["impact"] = f["impact"][:3]
    f["prior"] = _run(graph, "prior_triage", {"pr": pid, "related": related_ids(f)}, repo)
    return f


def related_ids(f: dict) -> list[str]:
    ids = [r["other"] for k in ("dup_shared_issue", "dup_file_overlap", "dup_similar_title")
           for r in f.get(k) or []]
    return list(dict.fromkeys(ids))


def labels_for(f: dict) -> list[str]:
    out = []
    if f.get("dup_shared_issue") or f.get("dup_similar_title"):
        out.append("possible-duplicate")
    if f.get("impact") or f.get("pr_bus_factor"):
        out.append("needs-review")
    return out


# ---------------------------------------------------------------- compose (pure)
def ref(pid: str, repo: str) -> str:
    """Render a PR/issue id for a comment. Never emits URLs or @mentions, so posting a comment
    can't create cross-references or notifications outside the demo repo."""
    r, _, n = pid.rpartition("#")
    if not n.isdigit():
        return f"`{pid}`"
    n = int(n)
    if r == repo and n >= HISTORY_OFFSET and repo in HISTORY_NAME:
        return f"`{HISTORY_NAME[repo]}#{n - HISTORY_OFFSET}`"
    return f"#{n}" if r == repo else f"`{pid}`"


def _details(paths) -> str:
    paths = [p for p in (paths if isinstance(paths, list) else [paths]) if p]
    if not paths:
        return ""
    body = "\n".join("  " + x for x in paths)
    return f"\n  <details><summary>graph path</summary>\n\n  ```\n{body}\n  ```\n  </details>"


def _title(t: str | None, repo: str = "") -> str:
    t = re.sub(r"[@`]", "", t or "")
    if repo in HISTORY_NAME:  # undo the history renumbering inside copied titles
        t = re.sub(r"#(\d+)", lambda m: f"{HISTORY_NAME[repo]}#{int(m.group(1)) - HISTORY_OFFSET}"
                   if int(m.group(1)) >= HISTORY_OFFSET else m.group(0), t)
    return f" \"{t[:80]}\"" if t else ""


def verdict(f: dict, repo: str) -> str:
    if f.get("dup_shared_issue"):
        r = f["dup_shared_issue"][0]
        return f"**Possible duplicate**: {ref(r['other'], repo)} already fixes {ref(r['via'], repo)}."
    if f.get("dup_similar_title"):
        r = f["dup_similar_title"][0]
        return f"**Possible duplicate**: very similar to {ref(r['other'], repo)} ({r['state'].lower()})."
    if f.get("impact") or f.get("pr_bus_factor"):
        return "**Needs a careful review**: this touches files with strong co-change partners or a single owner."
    return "**No red flags** in the graph for this PR."


def compose(repo: str, number: int, f: dict) -> str:
    out = [f"### PR Radar triage for #{number}", "", verdict(f, repo), ""]

    def section(title, rows, line):
        if rows:
            out.extend([f"**{title}**", ""])
            for r in rows:
                out.append(f"- {line(r)}{_details(r.get('evidence'))}")
            out.append("")

    section("Fixes the same issue", f.get("dup_shared_issue"),
            lambda r: f"{ref(r['other'], repo)}{_title(r.get('title'), repo)} ({r['state'].lower()}) "
                      f"also fixes {ref(r['via'], repo)}")
    section("Similar title", f.get("dup_similar_title"),
            lambda r: f"{ref(r['other'], repo)}{_title(r.get('title'), repo)} ({r['state'].lower()}), "
                      f"title overlap {r['jaccard']:.2f}")
    section("Same files", f.get("dup_file_overlap"),
            lambda r: f"{ref(r['other'], repo)}{_title(r.get('title'), repo)} touches "
                      f"{r['shared']}/{r['mine']} of these files")
    section("These usually change with this PR's files (not touched here)", f.get("impact"),
            lambda r: f"`{r['file']}` (changed together {int(r['strength'])}x)")
    section("Single-owner files", f.get("pr_bus_factor"),
            lambda r: f"`{r['file']}`: every earlier change ({r['prs']} PRs) went through one person "
                      f"(`{r['only_human']}`)")
    section("Suggested reviewers", f.get("reviewers"),
            lambda r: f"`{r['reviewer']}` (reviewed {r['reviewed']} PRs here, "
                      f"{r['files']} on these files)")
    if f.get("prior"):
        out.extend(["**Memory**", ""])
        for r in f["prior"]:
            out.append(f"- already flagged {ref(r['pr'], repo)} on {str(r['at'])[:10]}"
                       f" ({r.get('verdict') or 'triaged'}){_details(r.get('evidence'))}")
        out.append("")
    out.append("<sub>PR Radar: every claim above is a path in the FalkorDB PR graph. "
               "Reviewer names are not @-mentioned.</sub>")
    return "\n".join(out)


# ---------------------------------------------------------------- remember / act
def remember(graph, repo: str, number: int, f: dict, labels: list[str]) -> None:
    v = re.sub(r"\*", "", verdict(f, repo)).split(":")[0]
    graph.query(TRIAGE_QUERIES["remember_triage"], {
        "pr": f"{repo}#{number}", "tid": uuid.uuid4().hex,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "verdict": v, "labels": labels, "related": related_ids(f),
        "findings": json.dumps({k: [{x: y for x, y in r.items() if x != "evidence"} for r in rows]
                                for k, rows in f.items()}, default=str)})


def act(repo: str, number: int, body: str, labels: list[str]) -> None:
    if repo not in POST_ALLOWED:
        raise PermissionError(f"refusing to post on {repo}; allowed: {sorted(POST_ALLOWED)}")
    _gh("pr", "comment", str(number), "--repo", repo, "--body-file", "-", input=body)
    for lab in labels:
        subprocess.run(["gh", "label", "create", lab, "--repo", repo, "--force",
                        "--color", "d93f0b" if lab == "possible-duplicate" else "fbca04"],
                       capture_output=True, text=True)
        _gh("pr", "edit", str(number), "--repo", repo, "--add-label", lab)


def triage(repo: str, pr_number: int, post: bool = False, graph=None, memory: bool = True) -> dict:
    if post and repo not in POST_ALLOWED:
        raise PermissionError(f"refusing to post on {repo}; allowed: {sorted(POST_ALLOWED)}")
    if graph is None:
        from .db import connect
        graph = connect()
    ingest_pr(graph, repo, fetch_pr(repo, pr_number))
    f = gather(graph, repo, pr_number)
    body = compose(repo, pr_number, f)
    labels = labels_for(f)
    if memory:
        remember(graph, repo, pr_number, f, labels)
    if post:
        act(repo, pr_number, body, labels)
    return {"comment": body, "labels": labels, "findings": f}


# ---------------------------------------------------------------- demo history
def remap_raw(raw: dict, new_repo: str, offset: int = HISTORY_OFFSET) -> dict:
    """Copy of an ingest cache re-homed to new_repo, numbers shifted by offset.

    Same-repo '#N' links in titles/bodies are shifted too so FIXES/REFERENCES survive.
    """
    old = raw["repo"]
    shift = lambda m: f"#{int(m.group(1)) + offset}"  # noqa: E731

    def text(s):
        if not s:
            return s
        return re.sub(r"(?:" + re.escape(old) + r"|(?<![\w/&]))#(\d+)\b", shift, s, flags=re.I)

    def item(x):
        y = {**x, "number": x["number"] + offset, "title": text(x["title"]), "body": text(x.get("body"))}
        if "closingIssuesReferences" in x:
            y["closingIssuesReferences"] = {"nodes": [{"number": n["number"] + offset}
                                                      for n in (x["closingIssuesReferences"] or {}).get("nodes") or []]}
        return y

    return {"repo": new_repo, "pulls": [item(p) for p in raw["pulls"]],
            "issues": [item(i) for i in raw["issues"]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--post", action="store_true", help=f"comment + label (only {sorted(POST_ALLOWED)})")
    a = ap.parse_args(argv)
    res = triage(a.repo, a.pr, post=a.post)
    sys.stdout.reconfigure(encoding="utf-8")
    print(res["comment"])
    print(f"\nlabels: {res['labels']}{' (posted)' if a.post else ' (dry run)'}", file=sys.stderr)


if __name__ == "__main__":
    main()
