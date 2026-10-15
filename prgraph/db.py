"""FalkorDB connection + schema setup."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Read <repo>/.env into os.environ (existing env vars win). python-dotenv if present."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))


_load_dotenv()

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
