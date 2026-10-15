# Cypher queries

Generated from `prgraph/queries.py` and `prgraph/ingest.py`. Ids: PR/Issue `owner/repo#N`, File `owner/repo:path`, Repo `owner/repo`. Defaults: k=10, max_paths=3, min_overlap=0.5, min_jaccard=0.5.

## Question queries

### dup_shared_issue

Q1. Other PRs that fix/reference the same issue. Params: $pr.

```cypher
MATCH path = (p:PR {id:$pr})-[:FIXES|REFERENCES]->(i:Issue)<-[:FIXES|REFERENCES]-(o:PR)
WHERE o <> p
RETURN o.id AS other, o.state AS state, o.title AS title, i.id AS via, path AS evidence
ORDER BY o.created DESC
```

### dup_file_overlap

Q1. PRs touching >= $min_overlap of this PR's files.

```cypher
MATCH (p:PR {id:$pr})-[:TOUCHES]->(pf:File)
WITH p, count(pf) AS mine
MATCH path = (p)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(o:PR)
WHERE o <> p
WITH o, mine, count(DISTINCT f) AS shared, collect(path) AS paths
WHERE toFloat(shared) / mine >= $min_overlap
RETURN o.id AS other, o.state AS state, o.title AS title, shared, mine,
       paths[0..$max_paths] AS evidence
ORDER BY shared DESC LIMIT $k
```

### dup_similar_title

Q1. PRs/issues in the same repo whose title tokens overlap.

```cypher
MATCH path = (p:PR {id:$pr})-[:IN_REPO]->(:Repo)<-[:IN_REPO]-(o)
WHERE o <> p AND (o:PR OR o:Issue)
WITH o, path, size([t IN o.tokens WHERE t IN p.tokens]) AS common, size(p.tokens) AS n
WHERE n > 0 AND toFloat(common) / n >= $min_jaccard
RETURN o.id AS other, labels(o)[0] AS kind, o.state AS state, o.title AS title,
       common, path AS evidence
ORDER BY common DESC LIMIT $k
```

### issue_fixed_by

Q1 (issue form). Is issue $issue already being fixed?

```cypher
MATCH path = (i:Issue {id:$issue})<-[rel:FIXES|REFERENCES]-(p:PR)
OPTIONAL MATCH (a:Person)-[:AUTHORED]->(p)
RETURN p.id AS pr, p.state AS state, p.merged AS merged, type(rel) AS link,
       a.login AS author, path AS evidence
ORDER BY p.merged DESC
```

### reviewers

Q2. People who authored (1) or reviewed (2) PRs touching the same files; +3 per shared file.

```cypher
MATCH (p:PR {id:$pr})
OPTIONAL MATCH (me:Person)-[:AUTHORED]->(p)
MATCH path = (p)-[:TOUCHES]->(f:File)<-[:TOUCHES]-(o:PR)<-[r:AUTHORED|REVIEWED]-(cand:Person)
WHERE o <> p AND (me IS NULL OR cand <> me) AND NOT cand.login ENDS WITH '[bot]'
WITH cand, count(DISTINCT f) AS files, count(DISTINCT o) AS prs,
     sum(CASE type(r) WHEN 'REVIEWED' THEN 2 ELSE 1 END) AS weight, collect(path) AS paths
RETURN cand.login AS reviewer, files, prs, files * 3 + weight AS score,
       paths[0..$max_paths] AS evidence
ORDER BY score DESC LIMIT $k
```

### impact

Q3. Files historically co-changed with this PR's files but not in it.

```cypher
MATCH (p:PR {id:$pr})-[:TOUCHES]->(f:File)
MATCH path = (f)-[c:CO_CHANGED]-(g:File)
WHERE NOT (p)-[:TOUCHES]->(g)
WITH g, sum(c.weight) AS strength, collect(path) AS paths
RETURN g.path AS file, strength, paths[0..$max_paths] AS evidence
ORDER BY strength DESC LIMIT $k
```

### stale_open_prs

Q4. Oldest open PRs and how many reviewers they got.

```cypher
MATCH path = (a:Person)-[:AUTHORED]->(p:PR {state:'OPEN'})-[:IN_REPO]->(:Repo {id:$repo})
OPTIONAL MATCH (rv:Person)-[:REVIEWED]->(p)
RETURN p.id AS pr, p.title AS title, p.created AS created, a.login AS author,
       count(DISTINCT rv) AS reviewers, path AS evidence
ORDER BY created ASC LIMIT $k
```

### last_merges

Q4. Most recent merges (empty/old = stalled).

```cypher
MATCH path = (a:Person)-[:AUTHORED]->(p:PR)-[:IN_REPO]->(:Repo {id:$repo})
WHERE p.merged IS NOT NULL
RETURN p.id AS pr, p.merged AS merged, a.login AS author, path AS evidence
ORDER BY merged DESC LIMIT $k
```

### reviewer_load

Q4. Review concentration (bus factor).

```cypher
MATCH path = (rv:Person)-[:REVIEWED]->(p:PR)-[:IN_REPO]->(:Repo {id:$repo})
WITH rv, count(DISTINCT p) AS reviewed,
     count(DISTINCT CASE WHEN p.state = 'OPEN' THEN p END) AS open_reviewed,
     collect(path) AS paths
RETURN rv.login AS reviewer, reviewed, open_reviewed, paths[0..$max_paths] AS evidence
ORDER BY reviewed DESC LIMIT $k
```

## Ingestion (batched UNWIND, idempotent MERGE)

### load prs

```cypher
UNWIND $rows AS r
MERGE (p:PR {id:r.id})
SET p.number=r.number, p.title=r.title, p.state=r.state, p.created=r.created,
    p.merged=r.merged, p.closed=r.closed, p.url=r.url, p.tokens=r.tokens
WITH p
MATCH (repo:Repo {id:$repo}) MERGE (p)-[:IN_REPO]->(repo)
```

### load issues

```cypher
UNWIND $rows AS r
MERGE (i:Issue {id:r.id})
SET i.number=r.number, i.title=r.title, i.state=r.state, i.created=r.created,
    i.closed=r.closed, i.url=r.url, i.tokens=r.tokens
WITH i
MATCH (repo:Repo {id:$repo}) MERGE (i)-[:IN_REPO]->(repo)
```

### load authored

```cypher
UNWIND $rows AS r
MERGE (a:Person {login:r.login})
WITH a, r
OPTIONAL MATCH (p:PR {id:r.id}) WHERE r.kind = 'PR'
OPTIONAL MATCH (i:Issue {id:r.id}) WHERE r.kind = 'Issue'
WITH a, coalesce(p, i) AS t WHERE t IS NOT NULL
MERGE (a)-[:AUTHORED]->(t)
```

### load reviews

```cypher
UNWIND $rows AS r
MERGE (a:Person {login:r.login})
WITH a, r MATCH (p:PR {id:r.id})
MERGE (a)-[e:REVIEWED {state:r.state}]->(p)
SET e.at = r.at
```

### load touches

```cypher
UNWIND $rows AS r
MATCH (p:PR {id:r.id})
MERGE (f:File {id:r.fid}) SET f.path = r.path
MERGE (p)-[e:TOUCHES]->(f)
SET e.additions = r.add, e.deletions = r.del
```

### load fixes

```cypher
UNWIND $rows AS r
MATCH (p:PR {id:r.id})
MERGE (i:Issue {id:r.iid}) ON CREATE SET i.number = r.number
MERGE (p)-[:FIXES]->(i)
```

### load refs

```cypher
UNWIND $rows AS r
MATCH (p:PR {id:r.id})
MERGE (i:Issue {id:r.iid}) ON CREATE SET i.number = r.number
MERGE (p)-[:REFERENCES]->(i)
```

### load labels

```cypher
UNWIND $rows AS r
MERGE (l:Label {id:r.lid}) SET l.name = r.name
WITH l, r
OPTIONAL MATCH (p:PR {id:r.id})
OPTIONAL MATCH (i:Issue {id:r.id})
WITH l, coalesce(p, i) AS t WHERE t IS NOT NULL
MERGE (t)-[:HAS_LABEL]->(l)
```

### load cochange

```cypher
UNWIND $rows AS r
MATCH (a:File {id:r.a}), (b:File {id:r.b})
MERGE (a)-[e:CO_CHANGED]-(b)
SET e.weight = r.w
```
