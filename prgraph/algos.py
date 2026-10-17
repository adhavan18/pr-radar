"""Graph-algorithm tools on FalkorDB built-in procedures.

PageRank: `CALL algo.pageRank(label, relType) YIELD node, score` (docs.falkordb.com/algorithms).
Both args are filters on *all* nodes/edges, so we run unfiltered over the
Person-PR-File graph and keep Person nodes afterwards.
Shortest path: FalkorDB's `algo.SPpaths` procedure with relDirection 'both',
since Cypher shortestPath() there is directed-only.
"""

ALGOS: dict[str, str] = {
    # Who are the hubs? PageRank over everything, then rank people.
    "key_maintainers": """
CALL algo.pageRank(null, null) YIELD node, score
WITH node, score WHERE node:Person AND NOT coalesce(node.is_bot, false)
MATCH path = (node)-[:AUTHORED|REVIEWED]->(p:PR)-[:IN_REPO]->(:Repo {id:$repo})
WITH node, score, count(DISTINCT p) AS prs, collect(path) AS paths
RETURN node.login AS person, score, prs, paths[0..$max_paths] AS evidence
ORDER BY score DESC LIMIT $k""",

    # How is X connected to Y? X/Y may be PR/Issue/File ids or person logins.
    "connection": """
MATCH (a) WHERE a.id = $a OR a.login = $a
MATCH (b) WHERE b.id = $b OR b.login = $b
WITH a, b LIMIT 1
CALL algo.SPpaths({sourceNode: a, targetNode: b, relDirection: 'both', maxLen: 6, pathCount: 1}) YIELD path
RETURN length(path) AS hops, path AS evidence""",
}
