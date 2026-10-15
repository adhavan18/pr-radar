"""Pure parsing helpers (no server needed)."""
import re
from collections import Counter
from itertools import combinations

_CLOSE = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
FIXES_RE = re.compile(rf"\b{_CLOSE}:?\s+((?:[\w.-]+/[\w.-]+)?#\d+)", re.I)
REF_RE = re.compile(r"(?<![\w/&])((?:[\w.-]+/[\w.-]+)?#(\d+))\b")


def _local_num(token: str, repo: str | None) -> int | None:
    """'#12' -> 12; 'owner/repo#12' -> 12 only if it's the same repo."""
    if "/" in token.split("#")[0]:
        other = token.split("#")[0]
        if repo is None or other.lower() != repo.lower():
            return None
    return int(token.split("#")[1])


def extract_links(text: str | None, repo: str | None = None) -> tuple[set[int], set[int]]:
    """Return (fixes, references) issue numbers from a PR body/title.

    references excludes anything already in fixes.
    """
    if not text:
        return set(), set()
    # drop fenced code blocks so stack traces etc. don't create noise
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    fixes = {n for t in FIXES_RE.findall(text) if (n := _local_num(t, repo)) is not None}
    refs = {n for t, _ in REF_RE.findall(text) if (n := _local_num(t, repo)) is not None}
    return fixes, refs - fixes


def co_changes(prs: list[dict], max_files: int = 50, min_weight: int = 2) -> list[dict]:
    """File pairs changed together in the same PR, weighted by #PRs.

    PRs touching more than max_files files are skipped (mass refactors/formatting
    would otherwise connect everything to everything).
    """
    c: Counter = Counter()
    for pr in prs:
        files = sorted({f["path"] for f in pr.get("files", [])})
        if len(files) < 2 or len(files) > max_files:
            continue
        c.update(combinations(files, 2))
    return [{"a": a, "b": b, "w": w} for (a, b), w in c.items() if w >= min_weight]


def title_tokens(title: str) -> list[str]:
    stop = {"the", "a", "an", "to", "of", "in", "for", "and", "on", "with", "is", "fix", "add", "when"}
    return sorted({w for w in re.findall(r"[a-z0-9_]{3,}", (title or "").lower()) if w not in stop})
