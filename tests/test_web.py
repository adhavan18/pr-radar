import pytest
from fastapi.testclient import TestClient

from web.app import app

EXAMPLES = [
    "Who should review copperhead PR #243?",
    "Is PR #279 a duplicate?",
    "What breaks if src/kicad/sexp.ts changes?",
    "Why is this repo stalled?",
]


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("PRGRAPH_DEMO", "1")
    return TestClient(app)


def test_repos_demo(demo):
    r = demo.get("/api/repos").json()
    assert r["demo"] is True
    assert "copperheadhq/copperhead" in r["repos"]


@pytest.mark.parametrize("q", EXAMPLES)
def test_ask_examples(demo, q):
    r = demo.post("/api/ask", json={"question": q, "repo": "copperheadhq/copperhead"})
    assert r.status_code == 200
    d = r.json()
    assert d["answer"] and d["tool_calls"] and d["evidence_paths"]
    for c in d["tool_calls"]:
        assert {"tool", "params", "rows"} <= c.keys()
    for p in d["evidence_paths"]:
        ids = {n["id"] for n in p["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids for e in p["edges"])


def test_ask_fallback_by_kind(demo):
    d = demo.post("/api/ask", json={"question": "who can review PR #1?", "repo": "sqlfluff/sqlfluff"}).json()
    assert d["tool_calls"][0]["tool"] == "reviewers"


def test_ask_unknown_repo(demo):
    assert demo.post("/api/ask", json={"question": "x", "repo": "a/b"}).status_code == 404


def test_ask_empty(demo):
    assert demo.post("/api/ask", json={"question": " ", "repo": "a/b"}).status_code == 400


def test_index_served(demo):
    r = demo.get("/")
    assert r.status_code == 200 and "PR Radar" in r.text
    assert demo.get("/static/app.js").status_code == 200


def test_live_without_agent_is_503(monkeypatch):
    monkeypatch.delenv("PRGRAPH_DEMO", raising=False)
    import importlib
    real = importlib.import_module

    def fake(name, *a, **k):
        if name == "prgraph.agent":
            raise ImportError("no agent")
        return real(name, *a, **k)

    monkeypatch.setattr("web.app.importlib.import_module", fake)
    r = TestClient(app).post("/api/ask", json={"question": "q", "repo": "copperheadhq/copperhead"})
    assert r.status_code == 503
