"""Parameterised Cypher for the four PR Radar questions.

Every query returns an `evidence` column holding graph path(s) that justify the row.
Keep docs/QUERIES.md in sync (tests check that every query here appears there).
"""

QUERIES: dict[str, str] = {
    # ---- Q1: already fixed / duplicated? -------------------------------------
    "dup_shared_issue": """
MATCH path = (p:PR {id:$pr})-[:FIXES|REFERENCES]->(i:Issue)<-[:FIXES|REFERENCES]-(o:PR)
WHERE o <> p
RETURN o.id AS other, o.state AS state, o.title AS title, i.id AS via, path AS evidence
ORDER BY o.created DESC""",

    "dup_file_overlap": """
MATCH (p:PR {id:$pr})-[:TOUCHES]->(pf:File)
WITH p, count(pf) AS mine
MATCH path = (p)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(o:PR)
WHERE o <> p
WITH o, mine, count(DISTINCT f) AS shared, collect(path) AS paths
WHERE toFloat(shared) / mine >= $min_overlap
RETURN o.id AS other, o.state AS state, o.title AS title, shared, mine,
       paths[0..$max_paths] AS evidence
ORDER BY shared DESC LIMIT $k""",

    "dup_similar_title": """
MATCH path = (p:PR {id:$pr})-[:IN_REPO]->(:Repo)<-[:IN_REPO]-(o)
WHERE o <> p AND (o:PR OR o:Issue)
WITH o, path, size([t IN o.tokens WHERE t IN p.tokens]) AS common, size(p.tokens) AS n
WHERE n > 0 AND toFloat(common) / n >= $min_jaccard
RETURN o.id AS other, labels(o)[0] AS kind, o.state AS state, o.title AS title,
       common, path AS evidence
ORDER BY common DESC LIMIT $k""",

    "issue_fixed_by": """
MATCH path = (i:Issue {id:$issue})<-[rel:FIXES|REFERENCES]-(p:PR)
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(p)
RETURN p.id AS pr, p.state AS state, p.merged AS merged, type(rel) AS link,
       a.login AS author, path AS evidence
ORDER BY p.merged DESC""",

    # ---- Q2: who should review? ----------------------------------------------
    "reviewers": """
MATCH (p:PR {id:$pr})
OPTIONAL MATCH (me:Person)-[:AUTHORED]->(p)
MATCH path = (p)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(o:PR)<-[r:AUTHORED|REVIEWED]-(cand:Person)
WHERE o <> p AND (me IS NULL OR cand <> me) AND NOT cand.login ENDS WITH '[bot]'
WITH cand, count(DISTINCT f) AS files, count(DISTINCT o) AS prs,
     sum(CASE type(r) WHEN 'REVIEWED' THEN 2 ELSE 1 END) AS weight, collect(path) AS paths
RETURN cand.login AS reviewer, files, prs, files * 3 + weight AS score,
       paths[0..$max_paths] AS evidence
ORDER BY score DESC LIMIT $k""",

    # ---- Q3: blast radius ----------------------------------------------------
    "impact": """
MATCH (p:PR {id:$pr})-[:TOUCHES]->(f:File)
MATCH path = (f)-[c:CO_CHANGED]-(g:File)
WHERE NOT (p)-[:TOUCHES]->(g)
WITH g, sum(c.weight) AS strength, collect(path) AS paths
RETURN g.path AS file, strength, paths[0..$max_paths] AS evidence
ORDER BY strength DESC LIMIT $k""",

    # ---- Q4: why is the repo stalled? ----------------------------------------
    "stale_open_prs": """
MATCH path = (a:Person)-[:AUTHORED]->(p:PR {state:'OPEN'})-[:IN_REPO]->(:Repo {id:$repo})
OPTIONAL MATCH (rv:Person)-[:REVIEWED]->(p)
RETURN p.id AS pr, p.title AS title, p.created AS created, a.login AS author,
       count(DISTINCT rv) AS reviewers, path AS evidence
ORDER BY created ASC LIMIT $k""",

    "last_merges": """
MATCH path = (a:Person)-[:AUTHORED]->(p:PR)-[:IN_REPO]->(:Repo {id:$repo})
WHERE p.merged IS NOT NULL
RETURN p.id AS pr, p.merged AS merged, a.login AS author, path AS evidence
ORDER BY merged DESC LIMIT $k""",

    "reviewer_load": """
MATCH path = (rv:Person)-[:REVIEWED]->(p:PR)-[:IN_REPO]->(:Repo {id:$repo})
WITH rv, count(DISTINCT p) AS reviewed,
     count(DISTINCT CASE WHEN p.state = 'OPEN' THEN p END) AS open_reviewed,
     collect(path) AS paths
RETURN rv.login AS reviewer, reviewed, open_reviewed, paths[0..$max_paths] AS evidence
ORDER BY reviewed DESC LIMIT $k""",
}

DEFAULTS = {"k": 10, "max_paths": 3, "min_overlap": 0.5, "min_jaccard": 0.5}


def param_names(cypher: str) -> set[str]:
    import re
    return set(re.findall(r"\$([A-Za-z_]\w*)", cypher))


def run(graph, name: str, **params):
    q = QUERIES[name]
    needed = param_names(q)
    p = {k: v for k, v in {**DEFAULTS, **params}.items() if k in needed}
    missing = needed - p.keys()
    if missing:
        raise ValueError(f"{name}: missing params {sorted(missing)}")
    res = graph.query(q, p)
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    return [dict(zip(cols, row)) for row in res.result_set]
