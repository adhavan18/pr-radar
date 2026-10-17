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

# Collaboration projection used by ownership_clusters: Person -AUTHORED|REVIEWED-> PR -TOUCHES-> File.
# FalkorDB's algo.* procedures follow stored edge direction. Betweenness over this projection scores
# every Person 0 (people only have outgoing edges), so `bridges` runs on the person-person COLLAB
# edges instead, which ingest stores in both directions.
_LPA = ("CALL algo.labelPropagation({nodeLabels:['Person','PR','File'], "
        "relationshipTypes:['AUTHORED','REVIEWED','TOUCHES']}) YIELD node, communityId")

# Defaults for algo params not in queries.DEFAULTS. since='' means "all ingested history".
ALGO_DEFAULTS = {"since": "", "min_files": 3}

ALGOS.update({
    # Counterfactual: what if $person leaves? Files whose recent humans are only that person.
    "bus_factor": """
MATCH (me:Person {login:$person})-[:AUTHORED|REVIEWED]->(p:PR)-[:IN_REPO]->(:Repo {id:$repo})
WHERE p.created >= $since
MATCH (p)-[:TOUCHES]->(f:File)
WITH DISTINCT me, f
MATCH (h:Person)-[:AUTHORED|REVIEWED]->(q:PR)-[:TOUCHES]->(f)
WHERE q.created >= $since AND NOT coalesce(h.is_bot, false)
WITH me, f, collect(DISTINCT h) AS humans, count(DISTINCT q) AS prs
WHERE size(humans) = 1 AND humans[0] = me
MATCH path = (me)-[:AUTHORED|REVIEWED]->(:PR)-[:TOUCHES]->(f)
WITH f, prs, collect(path) AS paths
RETURN f.path AS file, prs, 0 AS remaining_humans, paths[0..$max_paths] AS evidence
ORDER BY prs DESC, file LIMIT $k""",

    # Repo-wide: files touched recently by exactly one non-bot human.
    "bus_factor_files": """
MATCH (q:PR)-[:IN_REPO]->(:Repo {id:$repo})
WHERE q.created >= $since
MATCH (h:Person)-[:AUTHORED|REVIEWED]->(q)-[:TOUCHES]->(f:File)
WHERE NOT coalesce(h.is_bot, false)
WITH f, collect(DISTINCT h) AS humans, count(DISTINCT q) AS prs
WHERE size(humans) = 1
WITH f, humans[0] AS owner, prs
MATCH path = (owner)-[:AUTHORED|REVIEWED]->(:PR)-[:TOUCHES]->(f)
WITH f, owner, prs, collect(path) AS paths
RETURN f.path AS file, owner.login AS only_human, prs, paths[0..$max_paths] AS evidence
ORDER BY prs DESC, file LIMIT $k""",

    # Code-ownership communities via label propagation over the person-PR-file graph.
    "ownership_clusters": _LPA + """
WITH node, communityId WHERE node:File AND node.id STARTS WITH ($repo + ':')
MATCH (q:PR)-[:TOUCHES]->(node)
WITH communityId, node, count(DISTINCT q) AS touches
ORDER BY touches DESC
WITH communityId, count(node) AS files, collect(node.path)[0..5] AS top_files
WHERE files >= $min_files
MATCH (h:Person)-[:AUTHORED|REVIEWED]->(q:PR)-[:TOUCHES]->(f:File)
WHERE f.id STARTS WITH ($repo + ':') AND f.path IN top_files AND NOT coalesce(h.is_bot, false)
WITH communityId, files, top_files, h, count(DISTINCT q) AS w
ORDER BY w DESC
WITH communityId, files, top_files, collect(h.login + ' (' + toString(w) + ')')[0..5] AS top_people
RETURN communityId AS cluster, files, top_files, top_people
ORDER BY files DESC LIMIT $k""",

    # Connectors: betweenness centrality over the person-person COLLAB graph (author <-> reviewer
    # of the same PR, stored in both directions so the directed algorithm sees it as undirected).
    "bridges": """
CALL algo.betweenness({nodeLabels:['Person'], relationshipTypes:['COLLAB']}) YIELD node, score
WITH node, score WHERE score > 0 AND NOT coalesce(node.is_bot, false)
MATCH path = (node)-[c:COLLAB {repo:$repo}]->(o:Person)
WITH node, score, count(DISTINCT o) AS collaborators, sum(c.weight) AS collabs, collect(path) AS paths
RETURN node.login AS person, score, collaborators, collabs, paths[0..$max_paths] AS evidence
ORDER BY score DESC, collabs DESC LIMIT $k""",
})
