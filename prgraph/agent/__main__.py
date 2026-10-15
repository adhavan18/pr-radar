"""python -m prgraph.agent "who should review PR 243" --repo owner/repo [--no-llm] [--json]"""
import argparse
import json


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prgraph.agent")
    ap.add_argument("question")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--no-llm", action="store_true", help="deterministic regex router, no API key needed")
    ap.add_argument("--session", help="memory session id (default: random)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from ..db import connect
    from .core import ask

    kw = {"session": a.session} if a.session and not a.no_llm else {}
    res = ask(connect(), a.question, a.repo, no_llm=a.no_llm, **kw)
    if a.json:
        print(json.dumps(res, indent=1, default=str))
        return
    print(res["answer"])
    if res["evidence_paths"]:
        print("\nevidence:")
        for p in res["evidence_paths"][:15]:
            print("  " + p)


if __name__ == "__main__":
    main()
