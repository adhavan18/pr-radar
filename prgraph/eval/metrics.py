"""Pure metric helpers for the backtest (no server needed)."""
import re


def hit_at(ranked: list, truth: set, k: int) -> float:
    return 1.0 if truth and any(x in truth for x in ranked[:k]) else 0.0


def reciprocal_rank(ranked: list, truth: set) -> float:
    for i, x in enumerate(ranked, 1):
        if x in truth:
            return 1.0 / i
    return 0.0


def recall_at(ranked: list, truth: set, k: int) -> float:
    return len(set(ranked[:k]) & truth) / len(truth) if truth else 0.0


def pair(a, b) -> tuple:
    return (a, b) if a <= b else (b, a)


def prf(pred: set, truth: set) -> dict:
    tp = len(pred & truth)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(truth) if truth else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"predicted": len(pred), "truth": len(truth), "tp": tp,
            "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}


SUPERSEDE_RE = re.compile(
    r"(?i)\b(?:duplicate of|dup of|superseded by|supersedes|replaces)\s*:?\s*#(\d+)")


def explicit_dup_refs(body) -> set:
    return {int(n) for n in SUPERSEDE_RE.findall(body or "")}


def mean(xs) -> float:
    xs = list(xs)
    return round(sum(xs) / len(xs), 3) if xs else 0.0
