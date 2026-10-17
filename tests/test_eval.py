from prgraph.eval.metrics import (explicit_dup_refs, hit_at, mean, pair, prf, recall_at,
                                  reciprocal_rank)


def test_hit_and_mrr():
    ranked = ["a", "b", "c"]
    assert hit_at(ranked, {"b"}, 1) == 0.0
    assert hit_at(ranked, {"b"}, 3) == 1.0
    assert hit_at(ranked, set(), 3) == 0.0
    assert reciprocal_rank(ranked, {"c", "b"}) == 0.5
    assert reciprocal_rank(ranked, {"z"}) == 0.0


def test_recall_at():
    assert recall_at(["x", "y", "z"], {"y", "q"}, 2) == 0.5
    assert recall_at(["x"], set(), 5) == 0.0


def test_pair_and_prf():
    assert pair("r#9", "r#10") == pair("r#10", "r#9")
    m = prf({pair(1, 2), pair(3, 4)}, {pair(2, 1), pair(5, 6)})
    assert m["tp"] == 1 and m["precision"] == 0.5 and m["recall"] == 0.5 and m["f1"] == 0.5
    assert prf(set(), set())["f1"] == 0.0


def test_explicit_dup_refs():
    body = "Supersedes #12. duplicate of #7; replaces: #3. see #99"
    assert explicit_dup_refs(body) == {12, 7, 3}
    assert explicit_dup_refs(None) == set()


def test_mean():
    assert mean([1, 0, True]) == 0.667
    assert mean([]) == 0.0
