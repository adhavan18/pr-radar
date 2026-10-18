# demo video script (about 3 minutes)

before recording: FalkorDB running with both repos ingested (`python scripts/smoke.py` passes), `uvicorn web.app:app` on :8000, Claude code open in the repo with the `pr-radar` MCP server added. if the DB is down, record the web part with `PRGRAPH_DEMO=1`.

| time | shot | action | say |
|------|------|--------|-----|
| 0:00-0:20 | GitHub open-PR list of sqlfluff | scroll the list | "maintainers of busy repos get duplicate PRs, don't know who should review, and can't see why things stall. pr radar puts the repo in a FalkorDB graph and answers with the paths that prove it." |
| 0:20-0:40 | README graph model diagram | point at person, PR, file, issue, CO_CHANGED | "people author and review PRs, PRs touch files and fix issues, files that change together get a CO_CHANGED edge. one graph." |
| 0:40-1:10 | web UI, repo sqlfluff | click "who should review sqlfluff PR #8550?" | "reviewers come from people who wrote or reviewed PRs touching the same files. each suggestion shows its evidence path: person, PR, file, back to this PR." expand one path. |
| 1:10-1:35 | web UI, repo copperhead | click "is PR #279 a duplicate?" | "three signals: shared linked issue, overlapping files, similar titles. the first two are 2-hop graph patterns a text search would miss." |
| 1:35-1:55 | web UI, copperhead | click "what breaks if src/kicad/sexp.ts changes?" | "CO_CHANGED edges come from history: these files changed together in n PRs, and here are the PRs." |
| 1:55-2:15 | web UI | click "why is this repo stalled?" | "oldest open PRs, last merges, reviewer load. if one person does most reviews, that's the bus factor. key maintainers come from FalkorDB PageRank." |
| 2:15-2:50 | Claude code terminal | type: "using pr-radar, who should review sqlfluff PR 8731 and is it a duplicate?" then "what do we already know about PR 8731?" | "the same tools are an MCP server, so any agent can use them. Claude calls the reviewer and duplicate queries; the finding is saved in the graph as a question node linked to the PR, and recall_about reads it back." |
| 2:50-3:00 | README "why a graph" section | static | "every answer is a Cypher traversal on FalkorDB. track 01, agents that act on connected data. queries and setup are in the repo." |
