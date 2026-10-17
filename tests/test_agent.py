import asyncio
from types import SimpleNamespace as NS

import pytest

from prgraph.agent import core
from prgraph.agent.core import ask, ask_llm, ask_rules, route
from prgraph.agent.tools import CYPHER, TOOL_SPECS, anthropic_tools, call_tool, qualify, to_jsonable
from prgraph.queries import DEFAULTS, param_names

REPO = "o/r"


class FakeGraph:
    """Records (cypher, params); returns canned rows chosen by a substring of the query."""

    def __init__(self, canned=None):
        self.calls = []
        self.canned = canned or {}

    def query(self, q, params=None):
        self.calls.append((q, params or {}))
        for needle, (header, rows) in self.canned.items():
            if needle in q:
                return NS(header=[[1, h] for h in header], result_set=rows)
        return NS(header=[], result_set=[])


# ---- fake FalkorDB objects for path rendering ----
class Node:
    def __init__(self, i, label, **props):
        self.id, self.labels, self.properties = i, [label], props


class Edge:
    def __init__(self, rel):
        self.relation = rel


class Path:
    def __init__(self, nodes, edges):
        self._n, self._e = nodes, edges

    def nodes(self):
        return self._n

    def edges(self):
        return self._e


def test_every_tool_has_cypher_and_spec():
    assert set(TOOL_SPECS) == set(CYPHER)
    for t in anthropic_tools():
        props = t["input_schema"]["properties"]
        assert set(t["input_schema"]["required"]) <= set(props)


def test_tool_params_cover_cypher():
    auto = set(DEFAULTS) | {"repo", "session", "qid", "at", "evidence"}
    for name, (_, props) in TOOL_SPECS.items():
        assert param_names(CYPHER[name]) <= set(props) | auto, name


def test_qualify():
    assert qualify(243, REPO) == "o/r#243"
    assert qualify("#7", REPO) == "o/r#7"
    assert qualify("alice", REPO) == "alice"
    assert qualify("x/y#1", REPO) == "x/y#1"


def test_dispatch_qualifies_and_renders_paths():
    p = Path([Node(1, "Person", login="al"), Node(2, "PR", id="o/r#9")], [Edge("AUTHORED")])
    g = FakeGraph({"cand.login AS reviewer": (["reviewer", "score", "evidence"], [["al", 5, [p]]])})
    rows = call_tool(g, "reviewers", {"pr": 243}, REPO)
    q, params = g.calls[0]
    assert params["pr"] == "o/r#243" and params["k"] == 10 and "repo" not in params
    assert rows == [{"reviewer": "al", "score": 5, "evidence": ["(Person:al)-[:AUTHORED]-(PR:o/r#9)"]}]


def test_unknown_tool():
    with pytest.raises(ValueError):
        call_tool(FakeGraph(), "drop_everything", {}, REPO)


def test_to_jsonable_node():
    assert to_jsonable(Node(3, "File", id="o/r:a.py")) == "File:o/r:a.py"


@pytest.mark.parametrize("q,tools,arg", [
    ("who should review PR 243", ["reviewers"], "243"),
    ("is PR #369 a duplicate?", ["dup_shared_issue", "dup_file_overlap", "dup_similar_title"], "369"),
    ("is 369 a dup", ["dup_shared_issue", "dup_file_overlap", "dup_similar_title"], "369"),
    ("why is the repo stalled", ["stale_open_prs", "last_merges", "reviewer_load"], None),
    ("what else is at risk if we merge 12", ["impact"], "12"),
    ("which PR fixed issue 55?", ["issue_fixed_by"], "55"),
    ("What breaks if src/kicad/sexp.ts changes?", ["impact_file"], "src/kicad/sexp.ts"),
    ("what else breaks if `a.py` changes", ["impact_file"], "a.py"),
    ("who are the key maintainers", ["key_maintainers"], None),
    ("what did we conclude about PR 243 before", ["recall_about"], "243"),
])
def test_router(q, tools, arg):
    plan = route(q)
    assert [t for t, _ in plan] == tools
    if arg:
        assert arg in plan[0][1].values()


def test_router_connection_and_miss():
    assert route("how is alice connected to o/r#5?") == [("connection", {"a": "alice", "b": "o/r#5"})]
    assert route("tell me a joke") == []


def test_ask_rules_structured():
    g = FakeGraph({"rv.login AS reviewer": (["reviewer", "reviewed", "evidence"], [["bob", 3, ["(p)"]]])})
    res = ask_rules(g, "why is the repo stalled?", REPO)
    assert [c["tool"] for c in res["tool_calls"]] == ["stale_open_prs", "last_merges", "reviewer_load"]
    assert all(c[1]["repo"] == REPO for c in g.calls)
    assert res["evidence_paths"] == ["(p)"] and "bob" in res["answer"]


def test_ask_without_key_uses_rules(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = ask(FakeGraph(), "who should review PR 1", REPO)
    assert res["tool_calls"][0]["tool"] == "reviewers"


# ---- memory ----
def test_remember_query_shape():
    g = FakeGraph()
    call_tool(g, "remember_finding", {"question": "dup?", "answer": "yes, of #3",
                                      "about": [369, "alice"], "evidence": ["o/r#3"]}, REPO, session="s1")
    q, p = g.calls[0]
    assert "MERGE (s:Session {id:$session})" in q and "[:ASKED_IN]" in q and "[:ABOUT]" in q
    assert p["session"] == "s1" and p["about"] == ["o/r#369", "alice"] and p["evidence"] == ["o/r#3"]
    assert p["qid"] and p["at"]


def test_recall_query_shape():
    g = FakeGraph()
    call_tool(g, "recall_about", {"ref": "369"}, REPO)
    q, p = g.calls[0]
    assert "(s:Session)<-[:ASKED_IN]-(q:Question)-[:ABOUT]->(n)" in q
    assert p == {"ref": "o/r#369", "k": 10}


# ---- LLM loop with a mocked client ----
class FakeClient:
    def __init__(self, scripted):
        self.scripted, self.sent = list(scripted), []
        self.messages = self

    def create(self, **kw):
        self.sent.append({**kw, "messages": list(kw["messages"])})
        return self.scripted.pop(0)


def _tool_use(i, name, inp):
    return NS(type="tool_use", id=i, name=name, input=inp)


def test_llm_loop():
    client = FakeClient([
        NS(stop_reason="tool_use", content=[_tool_use("t1", "recall_about", {"ref": 243}),
                                            _tool_use("t2", "reviewers", {"pr": 243})]),
        NS(stop_reason="tool_use", content=[_tool_use("t3", "connection", {})]),  # bad params -> error
        NS(stop_reason="end_turn", content=[NS(type="text", text="ask bob; (Person:bob)-[:REVIEWED]-(PR:o/r#1)")]),
    ])
    g = FakeGraph({"cand.login AS reviewer": (["reviewer", "evidence"], [["bob", ["(Person:bob)-[:REVIEWED]-(PR:o/r#1)"]]])})
    res = ask_llm(g, "who should review 243?", REPO, client=client, session="s")
    assert [c["tool"] for c in res["tool_calls"]] == ["recall_about", "reviewers", "connection"]
    assert res["tool_calls"][1]["rows"][0]["reviewer"] == "bob"
    assert res["evidence_paths"] == ["(Person:bob)-[:REVIEWED]-(PR:o/r#1)"]
    assert res["answer"].startswith("ask bob")
    first = client.sent[0]
    assert first["model"] == core.MODEL == "claude-sonnet-5-5"
    assert "Always call graph tools" in first["system"] and len(first["tools"]) == len(TOOL_SPECS)
    results = client.sent[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    err = client.sent[2]["messages"][-1]["content"][0]
    assert err["is_error"] and "missing params" in err["content"]


def test_llm_refuses_answer_without_tools():
    client = FakeClient([NS(stop_reason="end_turn", content=[NS(type="text", text="probably alice")])])
    res = ask_llm(FakeGraph(), "who?", REPO, client=client)
    assert res["tool_calls"] == [] and "alice" not in res["answer"]


# ---- MCP ----
def test_mcp_handle_and_listing():
    from prgraph import mcp_server
    g = FakeGraph()
    assert mcp_server.handle("last_merges", {"repo": "x/y", "k": 2}, graph=g) == "[]"
    assert g.calls[0][1] == {"repo": "x/y", "k": 2}
    mcp = pytest.importorskip("mcp")  # noqa: F841
    from mcp.types import ListToolsRequest
    server = mcp_server.build_server()
    res = asyncio.run(server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list")))
    names = {t.name for t in res.root.tools}
    assert names == set(TOOL_SPECS)


def test_impact_file_dispatch_qualifies_path():
    g = FakeGraph()
    call_tool(g, "impact_file", {"file": "src/a.py"}, REPO)
    call_tool(g, "impact_file", {"file": "o/r:src/b.py"}, REPO)
    assert [c[1]["file"] for c in g.calls] == ["o/r:src/a.py", "o/r:src/b.py"]
    assert g.calls[0][1]["k"] == 10 and g.calls[0][1]["max_paths"] == 3


def test_dup_similar_title_threshold_param():
    g = FakeGraph()
    call_tool(g, "dup_similar_title", {"pr": 5, "min_jaccard": 0.7}, REPO)
    assert g.calls[0][1]["min_jaccard"] == 0.7
    call_tool(g, "dup_similar_title", {"pr": 5}, REPO)
    assert g.calls[1][1]["min_jaccard"] == DEFAULTS["min_jaccard"]


def test_remember_with_empty_about_still_returns_question():
    q = CYPHER["remember_finding"]
    # UNWIND over an empty list would drop the row and lose the Question id
    assert "UNWIND" not in q and "FOREACH" in q and "collect(n)" in q
    g = FakeGraph({"q.id AS question": (["question", "linked"], [["qid1", 0]])})
    rows = call_tool(g, "remember_finding", {"question": "q", "answer": "a", "about": []}, REPO, session="s")
    assert g.calls[0][1]["about"] == [] and rows == [{"question": "qid1", "linked": 0}]


# ---- graph-native algos: bus factor, ownership clusters, bridges ----
@pytest.mark.parametrize("q,tool,params", [
    ("what if animesh-chouhan leaves?", "bus_factor", {"person": "animesh-chouhan"}),
    ("What happens if @keraion quits", "bus_factor", {"person": "keraion"}),
    ("bus factor of taslater", "bus_factor", {"person": "taslater"}),
    ("which files have a bus factor of one?", "bus_factor_files", {}),
    ("who are the bridges?", "bridges", {}),
    ("show ownership clusters", "ownership_clusters", {}),
    ("what are the code communities", "ownership_clusters", {}),
])
def test_route_graph_algos(q, tool, params):
    assert route(q) == [(tool, params)]


def test_bus_factor_dispatch_defaults():
    g = FakeGraph({"humans[0] = me": (["file", "prs", "remaining_humans", "evidence"], [["a.py", 3, 0, []]])})
    rows = call_tool(g, "bus_factor", {"person": "@al"}, REPO)
    q, params = g.calls[0]
    assert params == {"person": "al", "repo": REPO, "since": "", "k": 10, "max_paths": 3}
    assert rows[0]["file"] == "a.py" and rows[0]["remaining_humans"] == 0


def test_cluster_and_bridge_queries_use_graph_algos():
    assert "algo.labelPropagation" in CYPHER["ownership_clusters"]
    assert "algo.betweenness" in CYPHER["bridges"] and "COLLAB" in CYPHER["bridges"]
    for name in ("ownership_clusters", "bridges"):
        g = FakeGraph()
        call_tool(g, name, {}, REPO)
        assert g.calls[0][1]["repo"] == REPO
    assert FakeGraph and call_tool(FakeGraph(), "ownership_clusters", {}, REPO) == []


def test_bus_factor_requires_person():
    spec = {t["name"]: t for t in anthropic_tools()}
    assert spec["bus_factor"]["input_schema"]["required"] == ["person"]
    with pytest.raises(ValueError):
        call_tool(FakeGraph(), "bus_factor", {}, REPO)
