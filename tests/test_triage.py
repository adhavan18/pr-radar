"""Offline tests for PR Radar triage: comment composition, labels, posting guard, remap."""
import pytest

from prgraph import triage as T

REPO = "adhavan18/pr-radar-demo"


def findings(**kw):
    base = {k: [] for k in ("dup_shared_issue", "dup_file_overlap", "dup_similar_title",
                            "reviewers", "impact", "pr_bus_factor", "prior")}
    return {**base, **kw}


DUP = findings(
    dup_shared_issue=[{"other": f"{REPO}#3", "state": "OPEN", "title": "normalize @eol", "via": f"{REPO}#1",
                       "evidence": f"(PR:{REPO}#2)-[:FIXES]-(Issue:{REPO}#1)-[:FIXES]-(PR:{REPO}#3)"}],
    dup_similar_title=[{"other": f"{REPO}#1381", "state": "OPEN", "title": "fix CRLF", "jaccard": 0.71,
                        "evidence": "p"}],
    reviewers=[{"reviewer": "alice", "reviewed": 9, "files": 2, "evidence": ["x"]}],
    prior=[{"pr": f"{REPO}#3", "at": "2026-10-17T10:00:00+00:00", "verdict": "Possible duplicate",
            "evidence": "(Triage:t)-[:ON]-(PR:x)"}],
)


def test_compose_duplicate():
    c = T.compose(REPO, 2, DUP)
    assert "**Possible duplicate**: #3 already fixes #1." in c
    assert "`copperhead#381`" in c and "#1381" not in c.replace(f"{REPO}#1381", "")
    assert "already flagged #3 on 2026-10-17" in c
    assert c.count("<details>") == 4
    assert "@" not in c.replace("<sub>", "")[: c.index("<sub>")]  # no mentions
    assert "http" not in c
    assert "Single-owner" not in c and "usually change" not in c  # empty sections omitted
    assert T.labels_for(DUP) == ["possible-duplicate"]


def test_compose_impact():
    f = findings(impact=[{"file": "openspec/specs/SPEC.md", "strength": 24, "evidence": ["a", "b"]}])
    c = T.compose(REPO, 5, f)
    assert c.splitlines()[2].startswith("**Needs a careful review**")
    assert "`openspec/specs/SPEC.md` (changed together 24x)" in c
    assert T.labels_for(f) == ["needs-review"]


def test_compose_clean():
    c = T.compose(REPO, 7, findings())
    assert "No red flags" in c and "<details>" not in c
    assert T.labels_for(findings()) == []


def test_related_ids_dedup():
    f = findings(dup_shared_issue=[{"other": "a"}], dup_file_overlap=[{"other": "a"}, {"other": "b"}])
    assert T.related_ids(f) == ["a", "b"]


def test_post_guard():
    with pytest.raises(PermissionError):
        T.act("copperheadhq/copperhead", 1, "x", [])
    with pytest.raises(PermissionError):
        T.triage("someone/else", 1, post=True, graph=object())


def test_remap_raw():
    raw = {"repo": "o/r", "pulls": [{"number": 5, "title": "fixes #3", "body": "see o/r#4, color #fff",
                                     "closingIssuesReferences": {"nodes": [{"number": 3}]}}],
           "issues": [{"number": 3, "title": "bug", "body": None}]}
    out = T.remap_raw(raw, REPO, 1000)
    p = out["pulls"][0]
    assert out["repo"] == REPO and p["number"] == 1005 and p["title"] == "fixes #1003"
    assert p["body"] == "see #1004, color #fff"
    assert p["closingIssuesReferences"]["nodes"] == [{"number": 1003}]
    assert out["issues"][0]["number"] == 1003


class FakeGraph:
    def __init__(self):
        self.calls = []

    def query(self, q, p=None):
        self.calls.append((q, p))
        class R:
            header, result_set = [], []
        return R()


def test_ingest_pr_skips_aggregates():
    g = FakeGraph()
    pr = {"number": 2, "title": "t", "body": "fixes #1", "state": "OPEN", "createdAt": "x", "url": "u",
          "author": {"login": "me"}, "files": {"nodes": [{"path": "a", "additions": 1, "deletions": 0},
                                                         {"path": "b", "additions": 1, "deletions": 0}]},
          "closingIssuesReferences": {"nodes": [{"number": 1, "title": "bug", "state": "OPEN",
                                                 "createdAt": "x", "author": {"login": "me"}}]}}
    T.ingest_pr(g, REPO, pr)
    text = "\n".join(q for q, _ in g.calls)
    assert "CO_CHANGED" not in text and "COLLAB" not in text
    assert "MERGE (p:PR" in text and "MERGE (i:Issue" in text
