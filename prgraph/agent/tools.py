"""Tool registry shared by the LLM agent, the rule router and the MCP server."""
import datetime as _dt
import re
import uuid

from ..algos import ALGOS
from ..queries import DEFAULTS, QUERIES, param_names
from .memory import MEMORY

CYPHER = {**QUERIES, **ALGOS, **MEMORY}

_PR = {"type": ["string", "integer"], "description": "PR number or full id owner/repo#N"}
_K = {"type": "integer", "description": "max rows (default 10)"}

TOOL_SPECS: dict[str, tuple] = {
    "dup_shared_issue": ("Other PRs that fix/reference the same issue as this PR (duplicate check).", {"pr": _PR}),
    "dup_file_overlap": ("Other PRs touching mostly the same files as this PR (duplicate check).", {"pr": _PR, "k": _K}),
    "dup_similar_title": ("PRs/issues with similar titles to this PR (duplicate check).", {"pr": _PR, "k": _K}),
    "issue_fixed_by": ("PRs that fix or reference an issue.", {"issue": {**_PR, "description": "issue number or owner/repo#N"}}),
    "reviewers": ("Suggested reviewers for a PR: people who authored/reviewed PRs on the same files.", {"pr": _PR, "k": _K}),
    "impact": ("Blast radius: files historically co-changed with this PR's files but not in it.", {"pr": _PR, "k": _K}),
    "stale_open_prs": ("Oldest open PRs in the repo and how many reviewers each has.", {"k": _K}),
    "last_merges": ("Most recent merged PRs in the repo.", {"k": _K}),
    "reviewer_load": ("Reviewers ranked by PRs reviewed, with open PRs they reviewed.", {"k": _K}),
    "key_maintainers": ("Key people by PageRank over the Person-PR-File graph.", {"k": _K}),
    "connection": ("Shortest path between two entities (PR/issue id or number, file id, or person login).",
                   {"a": {"type": ["string", "integer"]}, "b": {"type": ["string", "integer"]}}),
    "recall_about": ("Earlier conclusions stored in graph memory about a PR/issue/file/person.",
                     {"ref": {"type": ["string", "integer"], "description": "PR/issue number or id, file id, or login"}}),
    "remember_finding": ("Store a conclusion in graph memory so later sessions can recall it.",
                         {"question": {"type": "string"}, "answer": {"type": "string"},
                          "about": {"type": "array", "items": {"type": ["string", "integer"]}},
                          "evidence": {"type": "array", "items": {"type": "string"},
                                       "description": "ids of nodes cited as evidence"}}),
}
REQUIRED = {"dup_shared_issue": ["pr"], "dup_file_overlap": ["pr"], "dup_similar_title": ["pr"],
            "issue_fixed_by": ["issue"], "reviewers": ["pr"], "impact": ["pr"], "connection": ["a", "b"],
            "recall_about": ["ref"], "remember_finding": ["question", "answer", "about"]}


def anthropic_tools() -> list[dict]:
    return [{"name": n, "description": d,
             "input_schema": {"type": "object", "properties": props, "required": REQUIRED.get(n, [])}}
            for n, (d, props) in TOOL_SPECS.items()]


def qualify(ref, repo: str | None):
    """'243' / 243 / '#243' -> 'owner/repo#243'. Other strings pass through."""
    s = str(ref).strip()
    m = re.fullmatch(r"#?(\d+)", s)
    if m and repo:
        return f"{repo}#{m.group(1)}"
    return s


def to_jsonable(v):
    """Rows may hold FalkorDB Node/Edge/Path objects; render them as plain data."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    if callable(getattr(v, "nodes", None)) and callable(getattr(v, "edges", None)):  # Path
        return render_path(v)
    if hasattr(v, "labels") and hasattr(v, "properties"):  # Node
        return node_key(v)
    if hasattr(v, "relation"):  # Edge
        return v.relation
    return str(v)


def node_key(n) -> str:
    props = n.properties or {}
    label = (list(n.labels) or ["?"])[0]
    return f"{label}:{props.get('id') or props.get('login') or props.get('path') or n.id}"


def render_path(p) -> str:
    nodes, edges = p.nodes(), p.edges()
    out = f"({node_key(nodes[0])})"
    for e, n in zip(edges, nodes[1:]):
        out += f"-[:{e.relation}]-({node_key(n)})"
    return out


def evidence_of(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        ev = r.get("evidence")
        for e in ev if isinstance(ev, list) else [ev]:
            if isinstance(e, str) and e not in out:
                out.append(e)
    return out


def call_tool(graph, name: str, params: dict, repo: str | None = None,
              session: str | None = None) -> list[dict]:
    if name not in CYPHER:
        raise ValueError(f"unknown tool {name}")
    p = dict(params or {})
    for key in ("pr", "issue", "a", "b", "ref"):
        if key in p:
            p[key] = qualify(p[key], repo)
    if name == "remember_finding":
        p["about"] = [qualify(x, repo) for x in p.get("about") or []]
        p.setdefault("evidence", [])
        p["session"] = session or p.get("session") or "default"
        p["qid"] = uuid.uuid4().hex
        p["at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    if repo:
        p.setdefault("repo", repo)
    q = CYPHER[name]
    needed = param_names(q)
    p = {k: v for k, v in {**DEFAULTS, **p}.items() if k in needed}
    missing = needed - p.keys()
    if missing:
        raise ValueError(f"{name}: missing params {sorted(missing)}")
    res = graph.query(q, p)
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
    return [to_jsonable(dict(zip(cols, row))) for row in res.result_set]
