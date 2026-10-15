# Demo video script (about 3 minutes)

Before recording: FalkorDB running with both repos ingested (`python scripts/smoke.py` passes), `uvicorn web.app:app` on :8000, Claude Code open in the repo with the `pr-radar` MCP server added. If the DB is down, record the web part with `PRGRAPH_DEMO=1`.

| Time | Shot | Action | Say |
|------|------|--------|-----|
| 0:00-0:20 | GitHub open-PR list of sqlfluff | Scroll the list | "Maintainers of busy repos get duplicate PRs, don't know who should review, and can't see why things stall. PR Radar puts the repo in a FalkorDB graph and answers with the paths that prove it." |
| 0:20-0:40 | README graph model diagram | Point at Person, PR, File, Issue, CO_CHANGED | "People author and review PRs, PRs touch files and fix issues, files that change together get a CO_CHANGED edge. One graph." |
| 0:40-1:10 | Web UI, repo sqlfluff | Click "Who should review sqlfluff PR #8550?" | "Reviewers come from people who wrote or reviewed PRs touching the same files. Each suggestion shows its evidence path: person, PR, file, back to this PR." Expand one path. |
| 1:10-1:35 | Web UI, repo copperhead | Click "Is PR #279 a duplicate?" | "Three signals: shared linked issue, overlapping files, similar titles. The first two are 2-hop graph patterns a text search would miss." |
| 1:35-1:55 | Web UI, copperhead | Click "What breaks if src/kicad/sexp.ts changes?" | "CO_CHANGED edges come from history: these files changed together in N PRs, and here are the PRs." |
| 1:55-2:15 | Web UI | Click "Why is this repo stalled?" | "Oldest open PRs, last merges, reviewer load. If one person does most reviews, that's the bus factor. Key maintainers come from FalkorDB PageRank." |
| 2:15-2:50 | Claude Code terminal | Type: "using pr-radar, who should review sqlfluff PR 8731 and is it a duplicate?" then "what do we already know about PR 8731?" | "The same tools are an MCP server, so any agent can use them. Claude calls the reviewer and duplicate queries; the finding is saved in the graph as a Question node linked to the PR, and recall_about reads it back." |
| 2:50-3:00 | README "Why a graph" section | Static | "Every answer is a Cypher traversal on FalkorDB. Track 01, agents that act on connected data. Queries and setup are in the repo." |
