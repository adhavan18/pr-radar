# PR Radar

An agent for open-source maintainers and contributors, built on FalkorDB for the Graph Hacks hackathon (track 01, agents that act on connected data). It loads a public GitHub repo's PRs, issues, files, authors and reviews into a graph and answers:

1. Is this PR/issue already fixed or duplicated? (shared linked issue, overlapping files, similar title)
2. Who should review this change? (people who authored/reviewed PRs touching the same files, ranked)
3. What else is at risk? (files historically co-changed)
4. Why is the repo stalled? (old open PRs, last merges, reviewer load)

Every answer comes back with the graph paths behind it.

Status: work in progress. Ingestion, schema and queries are written; the agent layer is not built yet.

## Setup

```sh
pip install falkordb pytest
docker run -p 6379:6379 -it --rm falkordb/falkordb   # or FalkorDB Cloud
export FALKORDB_HOST=localhost FALKORDB_PORT=6379    # FALKORDB_USERNAME / FALKORDB_PASSWORD optional
gh auth login                                        # ingestion uses `gh api graphql`

python -m prgraph.ingest sqlfluff/sqlfluff --limit 200   # uses data/sqlfluff_sqlfluff.json if cached
python -m pytest                                          # offline tests
```

```python
from prgraph.db import connect
from prgraph.queries import run
g = connect()
run(g, "reviewers", pr="sqlfluff/sqlfluff#1234")
```

## Graph model and queries

- Data model: [docs/MODEL.md](docs/MODEL.md)
- All Cypher (question queries and ingestion): [docs/QUERIES.md](docs/QUERIES.md)

## Data

Only public GitHub data, from the GraphQL API. Cached snapshots for `copperheadhq/copperhead` and `sqlfluff/sqlfluff` are in `data/`.

## AI assistance

This project was built with help from Claude Code (Anthropic). Code, queries and docs were drafted with AI assistance and reviewed by me.
