import json
import re
from pathlib import Path

import pytest

from prgraph.ingest import LOAD, to_rows
from prgraph.parse import co_changes, extract_links, title_tokens
from prgraph.queries import DEFAULTS, QUERIES, param_names

ROOT = Path(__file__).resolve().parent.parent


def test_fixes_and_refs():
    fixes, refs = extract_links("Fixes #12, closes #13. See #14 and also #12.\nresolved: #15")
    assert fixes == {12, 13, 15}
    assert refs == {14}


def test_cross_repo_refs_filtered():
    f, r = extract_links("fixes other/repo#5, fixes me/x#6, ref me/x#7", repo="me/x")
    assert f == {6} and r == {7}


def test_no_false_refs():
    f, r = extract_links("color #fff, a&#39;b, path/to#3 and ```\n#99\n```")
    assert f == set() and 99 not in r


def test_co_changes():
    prs = [{"files": [{"path": "a"}, {"path": "b"}, {"path": "c"}]},
           {"files": [{"path": "b"}, {"path": "a"}]},
           {"files": [{"path": p} for p in "abcdefghij"]}]  # mass change skipped
    got = co_changes(prs, max_files=5, min_weight=2)
    assert got == [{"a": "a", "b": "b", "w": 2}]


def test_title_tokens():
    assert title_tokens("Fix the crash in parser for UTF-8") == ["crash", "parser", "utf"]


def _balanced(q):
    return all(q.count(a) == q.count(b) for a, b in ("()", "[]", "{}"))


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_query_wellformed(name):
    q = QUERIES[name]
    assert _balanced(q)
    assert re.search(r"\bRETURN\b", q) and "evidence" in q
    known = set(DEFAULTS) | {"pr", "issue", "repo", "file"}
    assert param_names(q) <= known, param_names(q) - known


@pytest.mark.parametrize("key,q", LOAD)
def test_load_queries(key, q):
    assert _balanced(q)
    assert param_names(q) <= {"rows", "repo"}
    assert "MERGE" in q


def test_queries_documented():
    doc = (ROOT / "docs" / "QUERIES.md").read_text(encoding="utf-8")
    norm = lambda s: " ".join(s.split())
    for name, q in QUERIES.items():
        assert norm(q) in norm(doc), name


def test_to_rows_on_fixture():
    raw = {"repo": "o/r", "pulls": [{
        "number": 3, "title": "Fix parser crash", "body": "fixes #1, see #2", "state": "MERGED",
        "createdAt": "2026-01-01", "mergedAt": "2026-01-02", "closedAt": None, "url": "u",
        "author": None, "labels": {"nodes": [{"name": "bug"}]},
        "files": {"nodes": [{"path": "a.py", "additions": 1, "deletions": 0}]},
        "reviews": {"nodes": [{"author": {"login": "rv"}, "state": "APPROVED", "submittedAt": "x"}]},
        "closingIssuesReferences": {"nodes": [{"number": 2}]}}],
        "issues": [{"number": 1, "title": "crash", "body": "", "state": "OPEN", "createdAt": "x",
                    "closedAt": None, "url": "u", "author": {"login": "me"}, "labels": {"nodes": []}}]}
    rows = to_rows(raw)
    assert [r["number"] for r in rows["fixes"]] == [1, 2]
    assert rows["refs"] == []
    assert rows["authored"][0]["login"] == "ghost"
    assert rows["touches"][0]["fid"] == "o/r:a.py"


@pytest.mark.parametrize("cache", sorted((ROOT / "data").glob("*.json")))
def test_real_caches_flatten(cache):
    raw = json.loads(cache.read_text(encoding="utf-8"))
    rows = to_rows(raw)
    assert rows["prs"] and rows["touches"]
    ids = {r["id"] for r in rows["prs"]}
    assert all(r["id"] in ids for r in rows["fixes"] + rows["touches"])


# ---- bots ----
@pytest.mark.parametrize("login,typename,want", [
    ("coderabbitai", None, True), ("chatgpt-codex-connector", None, True), ("cubic-dev-ai", None, True),
    ("copilot-pull-request-reviewer", None, True), ("github-actions", None, True), ("greptile-apps", None, True),
    ("renovate[bot]", None, True), ("someapp", "Bot", True), ("alice", "User", False), ("ghost", None, False),
])
def test_is_bot(login, typename, want):
    from prgraph.parse import is_bot
    assert is_bot(login, typename) is want


def test_to_rows_marks_bots():
    raw = {"repo": "o/r", "issues": [], "pulls": [{
        "number": 1, "title": "t", "body": "", "state": "OPEN", "createdAt": "x", "url": "u",
        "author": {"login": "alice", "__typename": "User"},
        "reviews": {"nodes": [{"author": {"login": "coderabbitai"}, "state": "COMMENTED"},
                              {"author": {"login": "newapp", "__typename": "Bot"}, "state": "COMMENTED"},
                              {"author": {"login": "bob"}, "state": "APPROVED"}]}}]}
    people = {r["login"]: r["is_bot"] for r in to_rows(raw)["people"]}
    assert people == {"alice": False, "bob": False, "coderabbitai": True, "newapp": True}
    assert LOAD[0][0] == "people" and "is_bot" in LOAD[0][1]


def test_queries_filter_bots():
    from prgraph.algos import ALGOS
    for q in (QUERIES["reviewers"], QUERIES["reviewer_load"], ALGOS["key_maintainers"]):
        assert re.search(r"NOT coalesce\(\w+\.is_bot, false\)", q)


def test_demo_uses_shared_bot_list():
    from web import make_demo
    assert make_demo.is_bot("coderabbitai") and make_demo.is_bot("x[bot]") and not make_demo.is_bot("alice")


# ---- impact_file / similar titles ----
def test_impact_file_query():
    q = QUERIES["impact_file"]
    assert "(f:File {id:$file})-[c:CO_CHANGED]-(g:File)" in q
    assert "(f)<-[:TOUCHES]-(p:PR)-[:TOUCHES]->(g)" in q


def test_dup_similar_title_is_jaccard():
    q = QUERIES["dup_similar_title"]
    assert "toFloat(common) / (n + m - common) AS jaccard" in q
    assert "size(o.tokens) >= n * $min_jaccard" in q and "size(o.tokens) * $min_jaccard <= n" in q
    assert "(p)-[:IN_REPO]->(repo)<-[:IN_REPO]-(o)" in q  # same repo only


def _snap():
    from web.make_demo import Snapshot
    raw = {"repo": "o/r", "pulls": [
        {"number": n, "title": t, "body": "", "state": "OPEN", "createdAt": "2026-01-0%d" % n,
         "author": {"login": "a"}, "files": {"nodes": [{"path": p, "additions": 1, "deletions": 0} for p in fs]}}
        for n, t, fs in [(1, "parser crash utf8 input", ["a.py", "b.py"]),
                         (2, "parser crash utf8", ["a.py", "b.py"]),
                         (3, "parser speedup totally different words here", ["a.py", "c.py"])]],
        "issues": []}
    return Snapshot(raw)


def test_demo_jaccard_matches_definition():
    rows = _snap().q_dup_similar_title("o/r#1", t=0.5)
    assert [(r["other"], r["jaccard"]) for r in rows] == [("o/r#2", 0.75)]  # #3: J=1/9


def test_demo_impact_file_has_pr_evidence():
    s = _snap()
    s.cochange["a.py"]["b.py"] = 2
    row = s.q_impact_file("a.py")[0]
    assert row["file"] == "b.py" and len(row["evidence"]) == 2
    assert [e["type"] for e in row["evidence"][0]["edges"]] == ["TOUCHES", "TOUCHES"]


def test_demo_impact_uses_real_query():
    d = json.loads((ROOT / "web" / "demo_responses.json").read_text(encoding="utf-8"))
    for kinds in d["repos"].values():
        call = next(k for k in kinds if k["kind"] == "impact")["tool_calls"][0]
        assert call["tool"] == "impact_file" and call["cypher"] == QUERIES["impact_file"].strip()


def test_docs_generated_up_to_date():
    from prgraph.gendocs import render
    assert (ROOT / "docs" / "QUERIES.md").read_text(encoding="utf-8") == render()
