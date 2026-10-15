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
    known = set(DEFAULTS) | {"pr", "issue", "repo"}
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
