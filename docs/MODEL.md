# graph data model

graph name: `prgraph` (env `PRGRAPH_GRAPH`). all nodes are written with `MERGE` on their unique key, so ingest is idempotent and several repos can share one graph.

## nodes

| label  | key            | properties |
|--------|----------------|------------|
| repo   | `id` = `owner/repo` | name |
| PR     | `id` = `owner/repo#N` | number, title, state (OPEN/CLOSED/MERGED), created, merged, closed, url, tokens (title words) |
| issue  | `id` = `owner/repo#N` | number, title, state, created, closed, url, tokens. issues only referenced by a PR (outside the fetched window) exist with just `number`. |
| person | `login` | (`ghost` for deleted accounts) |
| file   | `id` = `owner/repo:path` | path |
| label  | `id` = `owner/repo:name` | name |

indexes: one range index per key above (`prgraph/db.py`).

## edges

| edge | from -> to | properties | source |
|------|-----------|------------|--------|
| IN_REPO | PR/issue -> repo | | |
| AUTHORED | person -> PR/issue | | author |
| REVIEWED | person -> PR | state (APPROVED/CHANGES_REQUESTED/COMMENTED/DISMISSED), at | reviews; one edge per distinct state |
| TOUCHES | PR -> file | additions, deletions | PR files (first 100) |
| FIXES | PR -> issue | | GitHub `closingIssuesReferences` + `fixes/closes/resolves #N` in title/body |
| REFERENCES | PR -> issue | | any other `#N` in title/body (code fences stripped, other repos ignored) |
| HAS_LABEL | PR/issue -> label | | |
| CO_CHANGED | file - file | weight = #PRs changing both | derived; PRs touching >50 files skipped, weight >= 2 kept |
| COLLAB | person -> person | repo, weight = #PRs where one authored and the other reviewed | derived, bots excluded; stored in both directions because `algo.betweenness` follows edge direction and has no undirected option |

note: GitHub PR and issue numbers share one sequence, so a `#N` that is really a PR becomes an issue node with that number. good enough for duplicate detection; could be resolved later by merging the nodes.

```
(Person)-[:AUTHORED]->(PR)-[:TOUCHES]->(File)-[:CO_CHANGED]-(File)
(Person)-[:REVIEWED]->(PR)-[:FIXES|REFERENCES]->(Issue)<-[:AUTHORED]-(Person)
(PR|Issue)-[:IN_REPO]->(Repo)   (PR|Issue)-[:HAS_LABEL]->(Label)
```
