"""Agent memory stored in the graph itself.

(:Question {id, text, answer, evidence, at})-[:ASKED_IN]->(:Session {id})
(:Question)-[:ABOUT]->(PR|Issue|File|Person)
"""
MEMORY: dict[str, str] = {
    "remember_finding": """
MERGE (s:Session {id:$session})
ON CREATE SET s.started = $at
CREATE (q:Question {id:$qid, text:$question, answer:$answer, evidence:$evidence, at:$at})
CREATE (q)-[:ASKED_IN]->(s)
WITH q
OPTIONAL MATCH (n) WHERE ((n:PR OR n:Issue OR n:File) AND n.id IN $about) OR (n:Person AND n.login IN $about)
WITH q, collect(n) AS targets
FOREACH (t IN targets | MERGE (q)-[:ABOUT]->(t))
RETURN q.id AS question, size(targets) AS linked""",

    "recall_about": """
MATCH path = (s:Session)<-[:ASKED_IN]-(q:Question)-[:ABOUT]->(n)
WHERE n.id = $ref OR n.login = $ref
RETURN q.text AS question, q.answer AS answer, q.evidence AS evidence_ids,
       s.id AS session, q.at AS at, path AS evidence
ORDER BY q.at DESC LIMIT $k""",
}
