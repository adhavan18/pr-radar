"""Agent entry points: LLM tool loop (Anthropic) and deterministic --no-llm router."""
import json
import os
import re
import uuid

from .tools import anthropic_tools, call_tool, evidence_of

MODEL = os.environ.get("PRGRAPH_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 8

SYSTEM = """You are PR Radar, an assistant for maintainers of the GitHub repo {repo}.
The repo's PRs, issues, files, people and reviews live in a FalkorDB graph.
Rules:
- Always call graph tools before answering. Never answer from prior knowledge.
- Answer only from rows the tools returned. If they return nothing, say the graph has no evidence.
- Cite the graph paths (the `evidence` column) behind every claim, e.g. (PR:x#1)-[:TOUCHES]-(File:x:a.py).
- Call recall_about first when the question is about a specific PR/issue/person, to reuse earlier conclusions.
- When you reach a conclusion about a specific PR/issue, store it with remember_finding.
- Be brief: a direct answer, then the supporting paths."""

# ---- deterministic router ----------------------------------------------------
_NUM = r"#?(\d+)"


def _n(m):
    return next(g for g in m.groups() if g)


ROUTES = [
    (re.compile(rf"\b(?:duplicate|dup|already (?:fixed|done)|same as)\b.*?{_NUM}|{_NUM}.*?\b(?:duplicate|dup)\b", re.I),
     lambda m: [("dup_shared_issue", {"pr": _n(m)}), ("dup_file_overlap", {"pr": _n(m)}),
                ("dup_similar_title", {"pr": _n(m)})]),
    (re.compile(r"\b(?:before|earlier|previously|recall|remember|conclude)\b.*?" + _NUM, re.I),
     lambda m: [("recall_about", {"ref": _n(m)})]),
    (re.compile(r"\breview(?:er|ers)?\b.*?" + _NUM, re.I),
     lambda m: [("reviewers", {"pr": _n(m)})]),
    (re.compile(r"\b(?:impact|risk|blast|affect\w*|co-?changed?)\b.*?" + _NUM, re.I),
     lambda m: [("impact", {"pr": _n(m)})]),
    (re.compile(r"\b(?:fix\w*|address\w*|resolve\w*)\b.*?\bissue\s+" + _NUM
                + r"|\bissue\s+" + _NUM + r".*\b(?:fix\w*|address\w*|resolve\w*)", re.I),
     lambda m: [("issue_fixed_by", {"issue": _n(m)})]),
    (re.compile(r"\bhow is\s+(\S+)\s+(?:connected|related|linked)\s+to\s+(\S+?)\??$", re.I),
     lambda m: [("connection", {"a": m.group(1).lstrip("@"), "b": m.group(2).lstrip("@")})]),
    (re.compile(r"\b(?:stall|stalled|stuck|slow|inactive|dead)\b", re.I),
     lambda m: [("stale_open_prs", {}), ("last_merges", {"k": 5}), ("reviewer_load", {})]),
    (re.compile(r"\bmaintainers?\b|\b(?:key|main|top|core)\s+(?:people|contributors?)\b", re.I),
     lambda m: [("key_maintainers", {})]),
]


def route(question: str) -> list[tuple[str, dict]]:
    for rx, plan in ROUTES:
        m = rx.search(question)
        if m:
            return plan(m)
    return []


def _summarise(calls: list[dict]) -> str:
    lines = []
    for c in calls:
        rows = c["rows"]
        args = ", ".join(f"{k}={v}" for k, v in c["params"].items())
        lines.append(f"{c['tool']}({args}): {len(rows)} row(s)")
        for r in rows[:5]:
            lines.append("  - " + ", ".join(f"{k}={v}" for k, v in r.items() if k != "evidence"))
    return "\n".join(lines)


def ask_rules(graph, question: str, repo: str) -> dict:
    plan = route(question)
    if not plan:
        return {"answer": "No rule matches this question; set ANTHROPIC_API_KEY for the LLM agent.",
                "tool_calls": [], "evidence_paths": []}
    calls = [{"tool": t, "params": p, "rows": call_tool(graph, t, p, repo)} for t, p in plan]
    paths = [e for c in calls for e in evidence_of(c["rows"])]
    return {"answer": _summarise(calls), "tool_calls": calls, "evidence_paths": paths}


# ---- LLM agent -----------------------------------------------------------------
def ask_llm(graph, question: str, repo: str, client=None, session: str | None = None) -> dict:
    if client is None:
        import anthropic
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    session = session or uuid.uuid4().hex[:12]
    messages = [{"role": "user", "content": question}]
    calls: list[dict] = []
    answer = ""
    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=MODEL, max_tokens=2048, system=SYSTEM.format(repo=repo),
                                      tools=anthropic_tools(), messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        answer = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        if resp.stop_reason != "tool_use" or not uses:
            break
        results = []
        for u in uses:
            try:
                rows = call_tool(graph, u.name, dict(u.input), repo, session=session)
                content, err = json.dumps(rows, default=str)[:20000], False
            except Exception as e:  # report back to the model instead of crashing
                rows, content, err = [], f"error: {e}", True
            calls.append({"tool": u.name, "params": dict(u.input), "rows": rows})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": content, "is_error": err})
        messages.append({"role": "user", "content": results})
    if not calls:
        answer = "The model answered without consulting the graph; no supported answer."
    paths = [e for c in calls for e in evidence_of(c["rows"])]
    return {"answer": answer, "tool_calls": calls, "evidence_paths": paths, "session": session}


def ask(graph, question: str, repo: str, no_llm: bool = False, **kw) -> dict:
    if no_llm or not os.environ.get("ANTHROPIC_API_KEY"):
        return ask_rules(graph, question, repo)
    return ask_llm(graph, question, repo, **kw)
