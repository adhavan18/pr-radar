"""Parameterised Cypher for the four PR Radar questions.

Every query returns an `evidence` column holding graph path(s) that justify the row.
Keep docs/QUERIES.md in sync (tests check that every query here appears there).
"""

QUERIES: dict[str, str] = {
    # ---- Q1: already fixed / duplicated? -------------------------------------
    # FIXES only (a bare "#N" mention is not a claim to fix it), and issues fixed by more than
    # $max_issue_prs PRs are treated as tracking issues and skipped.
    "dup_shared_issue": """
MATCH (p:PR {id:$pr})-[:FIXES]->(i:Issue)<-[:FIXES]-(x:PR)
WITH p, i, count(DISTINCT x) AS fixers WHERE fixers <= $max_issue_prs
MATCH path = (p)-[:FIXES]->(i)<-[:FIXES]-(o:PR)
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

    # Jaccard over the title token sets stored at ingest. Size filter first:
    # J >= t needs t*|p| <= |o| <= |p|/t, so most candidates are dropped
    # before any list intersection is computed.
    "dup_similar_title": """
MATCH (p:PR {id:$pr})-[:IN_REPO]->(repo:Repo)
WITH p, repo, size(p.tokens) AS n WHERE n > 0
MATCH path = (p)-[:IN_REPO]->(repo)<-[:IN_REPO]-(o)
WHERE o <> p AND o:PR AND o.tokens IS NOT NULL
  AND size(o.tokens) >= n * $min_jaccard AND size(o.tokens) * $min_jaccard <= n
WITH o, path, n, size(o.tokens) AS m, size([t IN o.tokens WHERE t IN p.tokens]) AS common
WHERE common > 0
WITH o, path, common, toFloat(common) / (n + m - common) AS jaccard
WHERE jaccard >= $min_jaccard
RETURN o.id AS other, labels(o)[0] AS kind, o.state AS state, o.title AS title,
       common, jaccard, path AS evidence
ORDER BY jaccard DESC, common DESC LIMIT $k""",

    "issue_fixed_by": """
MATCH path = (i:Issue {id:$issue})<-[rel:FIXES|REFERENCES]-(p:PR)
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(p)
RETURN p.id AS pr, p.state AS state, p.merged AS merged, type(rel) AS link,
       a.login AS author, path AS evidence
ORDER BY p.merged DESC""",

    # ---- Q2: who should review? ----------------------------------------------
    # Candidates = humans who reviewed anything in this repo (REVIEWED only; authorship is not a
    # review signal). score = 3*log(1+#PRs reviewed)  [prior: REVIEWED degree in the repo]
    #                       + sum over this PR's files they reviewed of 1/log(1+#PRs touching it)
    # [IDF, so CHANGELOG-style hub files count little]. Variants tried: docs/EVAL.md.
    "reviewers": """
MATCH (p:PR {id:$pr})-[:IN_REPO]->(repo:Repo)
OPTIONAL MATCH (me:Person)-[:AUTHORED]->(p)
WITH p, repo, me
MATCH (cand:Person)-[:REVIEWED]->(q:PR)-[:IN_REPO]->(repo)
WHERE q <> p AND (me IS NULL OR cand <> me) AND NOT coalesce(cand.is_bot, false)
WITH p, cand, count(DISTINCT q) AS reviewed, collect(q)[0] AS sample
OPTIONAL MATCH fp = (p)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(o:PR)<-[:REVIEWED]-(cand)
WHERE o <> p
WITH p, cand, reviewed, sample, f, collect(fp) AS fpaths
OPTIONAL MATCH (f)<-[:TOUCHES]-(x:PR)
WITH cand, reviewed, sample, f, fpaths, count(DISTINCT x) AS df
WITH cand, reviewed, sample,
     sum(CASE WHEN f IS NULL THEN 0.0 ELSE 1.0 / log(1.0 + df) END) AS expertise,
     count(f) AS files, collect(fpaths[0]) AS paths
MATCH rp = (cand)-[:REVIEWED]->(sample)
RETURN cand.login AS reviewer, files, reviewed, expertise,
       3.0 * log(1.0 + reviewed) + expertise AS score,
       (paths + [rp])[0..$max_paths] AS evidence
ORDER BY score DESC, reviewed DESC LIMIT $k""",

    # ---- Q3: blast radius ----------------------------------------------------
    "impact": """
MATCH (p:PR {id:$pr})-[:TOUCHES]->(f:File)
MATCH path = (f)-[c:CO_CHANGED]-(g:File)
WHERE NOT (p)-[:TOUCHES]->(g)
WITH g, sum(c.weight) AS strength, collect(path) AS paths
RETURN g.path AS file, strength, paths[0..$max_paths] AS evidence
ORDER BY strength DESC LIMIT $k""",

    "impact_file": """
MATCH (f:File {id:$file})-[c:CO_CHANGED]-(g:File)
OPTIONAL MATCH path = (f)<-[:TOUCHES]-(p:PR)-[:TOUCHES]->(g)
WITH g, c.weight AS strength, collect(path) AS paths
RETURN g.path AS file, strength, paths[0..$max_paths] AS evidence
ORDER BY strength DESC, file ASC LIMIT $k""",

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
WHERE NOT coalesce(rv.is_bot, false)
WITH rv, count(DISTINCT p) AS reviewed,
     count(DISTINCT CASE WHEN p.state = 'OPEN' THEN p END) AS open_reviewed,
     collect(path) AS paths
RETURN rv.login AS reviewer, reviewed, open_reviewed, paths[0..$max_paths] AS evidence
ORDER BY reviewed DESC LIMIT $k""",
    # ---- Cross-repo (Person nodes are shared by login across every ingested repo) ----------
    "shared_contributors": """
MATCH (a:Person)-[:AUTHORED|REVIEWED]->(p:PR)-[:IN_REPO]->(r:Repo)
WHERE NOT coalesce(a.is_bot, false) AND a.login <> 'ghost'
WITH a, r, count(DISTINCT p) AS prs, collect(p)[0] AS sample
ORDER BY prs DESC
WITH a, collect(r.id + ' (' + toString(prs) + ')') AS repos, collect(sample) AS samples, sum(prs) AS total
WHERE size(repos) >= 2
UNWIND samples AS s
MATCH path = (a)-[:AUTHORED|REVIEWED]->(s)-[:IN_REPO]->(:Repo)
WITH a, repos, total, s, collect(path)[0] AS one
WITH a, repos, total, collect(one) AS paths
RETURN a.login AS person, size(repos) AS n_repos, repos, total AS prs, paths[0..$max_paths] AS evidence
ORDER BY n_repos DESC, prs DESC, person LIMIT $k""",

    # Topic = this PR's title tokens + tokens of its file paths (dirs, stems, extensions), minus
    # generic path words. Candidates reviewed PRs in OTHER repos sharing >= 2 of those tokens.
    "cross_repo_expert": """
MATCH (p:PR {id:$pr})-[:IN_REPO]->(home:Repo)
OPTIONAL MATCH (me:Person)-[:AUTHORED]->(p)
OPTIONAL MATCH (p)-[:TOUCHES]->(f:File)
WITH p, home, me, collect(f.path) AS fps
WITH p, home, me, coalesce(p.tokens, []) + reduce(acc = [], fp IN fps | acc + split(replace(replace(replace(replace(toLower(fp),'.','/'),'_','/'),'-','/'),' ','/'),'/')) AS raw
UNWIND raw AS t
WITH p, home, me, t WHERE size(t) >= 3 AND NOT t IN ['src','lib','tests','test','docs','doc','init','main','index','crates','github','workflows','snap','snapshots','resources','fixtures','txt','json','toml','yml','yaml','lock','cfg','ini','rst','html','css','png','svg','the','and','for','with','from','add','fix','update','use','when','into','support','allow','make','new','remove','handle','change','improve','agents','claude','contributing','readme','changelog','license','commit','pre','precommit','config','setup','pyproject','workflow','release','version','bump','deps','dependency','dependencies']
WITH p, home, me, collect(DISTINCT t) AS topic
MATCH (cand:Person)-[:REVIEWED]->(q:PR)-[:IN_REPO]->(other:Repo)
WHERE other <> home AND NOT coalesce(cand.is_bot, false) AND (me IS NULL OR cand <> me)
WITH DISTINCT home, topic, cand, q, other
OPTIONAL MATCH (q)-[:TOUCHES]->(g:File)
WITH home, topic, cand, q, other, collect(g.path) AS gps
WITH home, topic, cand, q, other, coalesce(q.tokens, []) + reduce(acc = [], gp IN gps | acc + split(replace(replace(replace(replace(toLower(gp),'.','/'),'_','/'),'-','/'),' ','/'),'/')) AS qraw
WITH home, cand, q, other, [t IN topic WHERE t IN qraw] AS hits
WHERE size(hits) >= 2
WITH home, cand, q, other, hits ORDER BY size(hits) DESC
WITH home, cand, collect(DISTINCT other.id) AS repos, count(DISTINCT q) AS prs, collect(q)[0] AS best,
     collect(hits) AS allhits
WITH home, cand, repos, prs, best, reduce(acc = [], h IN allhits | acc + [x IN h WHERE NOT x IN acc]) AS matched
MATCH path = (cand)-[:REVIEWED]->(best)-[:IN_REPO]->(:Repo)
WITH home, cand, repos, prs, matched, collect(path)[0] AS ev
OPTIONAL MATCH (cand)-[:AUTHORED|REVIEWED]->(h:PR)-[:IN_REPO]->(home)
WITH cand, repos, prs, matched, ev, count(DISTINCT h) AS here
RETURN cand.login AS person, repos, prs AS matching_reviews, matched[0..8] AS topics, here AS prs_here,
       size(matched) + log(1.0 + prs) AS score, [ev] AS evidence
ORDER BY score DESC, person LIMIT $k""",
}

DEFAULTS = {"k": 10, "max_paths": 3, "min_overlap": 0.5, "min_jaccard": 0.4, "max_issue_prs": 3}

# One-line descriptions used by `python -m prgraph.gendocs` for docs/QUERIES.md.
DOCS: dict[str, str] = {
    "dup_shared_issue": "Q1. Other PRs that FIX the same issue (REFERENCES ignored; issues fixed by "
                        "> $max_issue_prs PRs are tracking issues and skipped). Params: $pr.",
    "dup_file_overlap": "Q1. PRs touching >= $min_overlap of this PR's files.",
    "dup_similar_title": "Q1. Other PRs in the same repo with title-token Jaccard >= $min_jaccard "
                         "(size-filtered candidates).",
    "issue_fixed_by": "Q1 (issue form). Is issue $issue already being fixed?",
    "reviewers": "Q2. Humans who reviewed in this repo: 3*log(1+#PRs reviewed) + IDF-weighted "
                 "count of this PR's files they reviewed before. Author and bots excluded.",
    "impact": "Q3. Files historically co-changed with this PR's files but not in it.",
    "impact_file": "Q3 (file form). What breaks if $file changes: co-changed files, with the PRs "
                   "that changed both as evidence.",
    "stale_open_prs": "Q4. Oldest open PRs and how many reviewers they got.",
    "last_merges": "Q4. Most recent merges (empty/old = stalled).",
    "shared_contributors": "Cross-repo. People (non-bot) who authored/reviewed PRs in 2+ ingested repos, "
                           "with per-repo PR counts. Only possible because Person nodes are shared by login.",
    "cross_repo_expert": "Cross-repo. For PR $pr: people who reviewed PRs in OTHER repos whose title/file-path "
                         "tokens share >= 2 words with this PR's (topic transfer); prs_here = their activity in this repo.",
    "reviewer_load": "Q4. Review concentration (bus factor), bots excluded.",
}


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
