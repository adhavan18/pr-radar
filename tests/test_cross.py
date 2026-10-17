"""Cross-repo tools: routing, registry wiring and the offline mirror in web/cross.py."""
from prgraph.agent.core import route
from prgraph.agent.tools import REQUIRED, TOOL_SPECS
from prgraph.queries import DOCS, QUERIES
from web.cross import Cross, topic


def _rows(repo, prs, reviews, touches, people):
    return {"people": [{"login": p, "is_bot": False} for p in people],
            "prs": [{"id": f"{repo}#{n}", "number": n, "title": t, "tokens": t.lower().split()} for n, t in prs],
            "authored": [{"login": a, "id": f"{repo}#{n}", "kind": "PR"} for n, a in reviews.get("authors", [])],
            "reviews": [{"login": r, "id": f"{repo}#{n}"} for n, r in reviews.get("reviews", [])],
            "touches": [{"id": f"{repo}#{n}", "path": p} for n, p in touches]}


def _cx():
    a = _rows("o/a", [(1, "async router validation"), (2, "docs")],
              {"authors": [(1, "alice"), (2, "bob")], "reviews": [(1, "carol")]},
              [(1, "pkg/routing.py")], ["alice", "bob", "carol"])
    b = _rows("o/b", [(7, "router validation speedup")],
              {"authors": [(7, "dave")], "reviews": [(7, "bob"), (7, "erin")]},
              [(7, "src/routing.rs")], ["bob", "dave", "erin"])
    return Cross({"o/a": a, "o/b": b})


def test_registered():
    for name in ("shared_contributors", "cross_repo_expert"):
        assert name in QUERIES and name in DOCS and name in TOOL_SPECS
    assert REQUIRED["cross_repo_expert"] == ["pr"]


def test_routes():
    assert route("Who works across repos?") == [("shared_contributors", {})]
    assert route("shared contributors") == [("shared_contributors", {})]
    assert route("Who reviewed similar work in other repos for PR #42?") == [("cross_repo_expert", {"pr": "42"})]
    assert route("cross-repo experts for 7") == [("cross_repo_expert", {"pr": "7"})]
    assert route("Who should review PR #42?") == [("reviewers", {"pr": "42"})]


def test_topic_drops_generic_words():
    assert topic(["fix", "router"], ["src/routing.rs", "tests/test_x.py"]) >= {"router", "routing"}
    assert not topic([], ["src/tests/docs.md"]) & {"src", "tests", "docs"}


def test_shared_contributors_offline():
    rows = _cx().shared_contributors()
    assert [r["person"] for r in rows] == ["bob"]
    assert rows[0]["n_repos"] == 2 and rows[0]["evidence"]


def test_cross_repo_expert_offline():
    rows = _cx().cross_repo_expert("o/a#1")
    who = {r["person"]: r for r in rows}
    assert set(who) == {"bob", "erin"}  # reviewed o/b#7 (router, validation, routing); dave only authored
    assert who["erin"]["prs_here"] == 0 and who["bob"]["prs_here"] == 1
    assert "router" in who["erin"]["topics"]
