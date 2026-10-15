"""FalkorDB connection + schema setup."""
import os

GRAPH_NAME = os.environ.get("PRGRAPH_GRAPH", "prgraph")

INDEXES = [
    "CREATE INDEX FOR (r:Repo) ON (r.id)",
    "CREATE INDEX FOR (p:PR) ON (p.id)",
    "CREATE INDEX FOR (i:Issue) ON (i.id)",
    "CREATE INDEX FOR (p:Person) ON (p.login)",
    "CREATE INDEX FOR (f:File) ON (f.id)",
    "CREATE INDEX FOR (l:Label) ON (l.id)",
]


def connect(graph_name: str = GRAPH_NAME):
    from falkordb import FalkorDB

    db = FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )
    return db.select_graph(graph_name)


def ensure_schema(graph) -> None:
    for q in INDEXES:
        try:
            graph.query(q)
        except Exception as e:  # index already exists
            if "already" not in str(e).lower():
                raise
