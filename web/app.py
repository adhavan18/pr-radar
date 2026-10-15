"""PR Radar web UI.

    uvicorn web.app:app --reload            # live, needs FalkorDB + prgraph.agent
    PRGRAPH_DEMO=1 uvicorn web.app:app      # canned answers from web/demo_responses.json
"""
from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
DEMO_FILE = HERE / "demo_responses.json"

app = FastAPI(title="PR Radar")


class Ask(BaseModel):
    question: str
    repo: str


def demo_mode() -> bool:
    return os.environ.get("PRGRAPH_DEMO", "") not in ("", "0", "false")


def _demo() -> dict:
    return json.loads(DEMO_FILE.read_text(encoding="utf-8"))["repos"]


def _kind(q: str) -> str | None:
    q = q.lower()
    if "review" in q:
        return "reviewers"
    if "dup" in q or "already fixed" in q:
        return "duplicate"
    if "break" in q or "impact" in q or "risk" in q:
        return "impact"
    if "stall" in q or "stuck" in q or "slow" in q:
        return "stalled"
    return None


def demo_answer(question: str, repo: str) -> dict:
    repos = _demo()
    if repo not in repos:
        raise HTTPException(404, f"no demo data for {repo}")
    items = repos[repo]
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()
    hit = next((x for x in items if norm(x["question"]) == norm(question)), None)
    if hit is None:
        kind = _kind(question)
        hit = next((x for x in items if x["kind"] == kind), None)
        if hit is None:
            return {"answer": "Demo mode only answers the four example question types "
                              "(reviewers, duplicates, impact, stalled).",
                    "tool_calls": [], "evidence_paths": [], "demo": True}
        hit = {**hit, "answer": f"(Demo: closest canned question is \"{hit['question']}\")\n\n" + hit["answer"]}
    return {**hit, "demo": True}


@app.get("/api/repos")
def repos():
    if demo_mode():
        names = list(_demo())
    else:
        names = []
        for f in sorted(DATA.glob("*.json")):
            try:
                with f.open(encoding="utf-8") as fh:
                    head = fh.read(400)
                m = re.search(r'"repo"\s*:\s*"([^"]+)"', head)
                names.append(m.group(1) if m else f.stem.replace("_", "/", 1))
            except OSError:
                continue
    return {"repos": names, "demo": demo_mode()}


@app.post("/api/ask")
def ask(body: Ask):
    if not body.question.strip():
        raise HTTPException(400, "question is empty")
    if demo_mode():
        return demo_answer(body.question, body.repo)
    try:
        agent = importlib.import_module("prgraph.agent")
        fn = getattr(agent, "ask")
    except (ImportError, AttributeError) as e:
        raise HTTPException(503, f"agent unavailable ({e}). Set PRGRAPH_DEMO=1 to use canned answers.")
    try:
        return fn(body.question, body.repo)
    except Exception as e:  # surface DB/LLM failures to the UI instead of a bare 500
        raise HTTPException(502, f"agent error: {type(e).__name__}: {e}")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")
