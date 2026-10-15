# Graph data model

Graph name: `prgraph` (env `PRGRAPH_GRAPH`). All nodes are written with `MERGE` on their unique key, so ingest is idempotent and several repos can share one graph.

## Nodes

| Label  | Key            | Properties |
|--------|----------------|------------|
| Repo   | `id` = `owner/repo` | name |
| PR     | `id` = `owner/repo#N` | number, title, state (OPEN/CLOSED/MERGED), created, merged, closed, url, tokens (title words) |
| Issue  | `id` = `owner/repo#N` | number, title, state, created, closed, url, tokens. Issues only referenced by a PR (outside the fetched window) exist with just `number`. |
| Person | `login` | (`ghost` for deleted accounts) |
| File   | `id` = `owner/repo:path` | path |
| Label  | `id` = `owner/repo:name` | name |

Indexes: one range index per key above (`prgraph/db.py`).

## Edges

| Edge | From -> To | Properties | Source |
|------|-----------|------------|--------|
| IN_REPO | PR/Issue -> Repo | | |
| AUTHORED | Person -> PR/Issue | | author |
| REVIEWED | Person -> PR | state (APPROVED/CHANGES_REQUESTED/COMMENTED/DISMISSED), at | reviews; one edge per distinct state |
| TOUCHES | PR -> File | additions, deletions | PR files (first 100) |
| FIXES | PR -> Issue | | GitHub `closingIssuesReferences` + `fixes/closes/resolves #N` in title/body |
| REFERENCES | PR -> Issue | | any other `#N` in title/body (code fences stripped, other repos ignored) |
| HAS_LABEL | PR/Issue -> Label | | |
| CO_CHANGED | File - File | weight = #PRs changing both | derived; PRs touching >50 files skipped, weight >= 2 kept |

Note: GitHub PR and issue numbers share one sequence, so a `#N` that is really a PR becomes an Issue node with that number. Good enough for duplicate detection; could be resolved later by merging the nodes.

```
(Person)-[:AUTHORED]->(PR)-[:TOUCHES]->(File)-[:CO_CHANGED]-(File)
(Person)-[:REVIEWED]->(PR)-[:FIXES|REFERENCES]->(Issue)<-[:AUTHORED]-(Person)
(PR|Issue)-[:IN_REPO]->(Repo)   (PR|Issue)-[:HAS_LABEL]->(Label)
```
