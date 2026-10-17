"""Build the public PR Radar demo: adhavan18/pr-radar-demo.

    python scripts/make_demo_repo.py            # everything below, idempotent
    python scripts/make_demo_repo.py --no-post  # set up, triage as dry run

1. Load copperheadhq/copperhead's cached history (data/copperheadhq_copperhead.json) into the
   graph re-homed to adhavan18/pr-radar-demo, numbers +1000 so they never collide with the demo's
   own issues/PRs (rendered as `copperhead#N` in comments).
2. Create the public repo with a snapshot of copperhead's file tree (Apache-2.0, LICENSE kept,
   README credits the original; .github/ dropped so upstream CI doesn't run there).
3. Open one demo issue and three PRs:
   a1  near-duplicate of copperhead#381 (same files, similar title, fixes the demo issue)
   a2  a second, smaller fix for the same demo issue (so the shared-issue check has a real match)
   b   touches src/agent/loop.ts but not its co-change partners
4. Run triage on each (posting comments/labels only on the demo repo).
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prgraph.ingest import DATA, load  # noqa: E402
from prgraph.triage import remap_raw, triage  # noqa: E402

SRC = "copperheadhq/copperhead"
DEMO = "adhavan18/pr-radar-demo"
ISSUE_TITLE = "edit_file corrupts files with CRLF line endings"

README = f"""# pr-radar-demo

A **demo copy** of [{SRC}](https://github.com/{SRC}) used to show
[PR Radar](https://github.com/adhavan18/pr-radar) triaging pull requests from a FalkorDB graph.

- The file tree is a snapshot of `{SRC}` at commit `{{sha}}`. All credit for the code goes to
  the copperhead authors; it is redistributed under its original Apache-2.0 license (see `LICENSE`).
  The original README is kept as `README.copperhead.md`.
- PR Radar's graph holds copperhead's real PR history for this repo id (renumbered +1000, shown
  as `copperhead#N`), so triage comments here are backed by real history.
- Issues and PRs in this repo are demo fixtures. Please send real contributions upstream.
"""

PRS = [
    {"branch": "fix-crlf-edit-file", "title": "fix: handle CRLF line endings in edit_file and schematic check",
     "body": "edit_file matched on LF only, so files with CRLF endings were rewritten wrong.\n\nfixes #{issue}",
     "edits": {"src/agent/filetools.ts": "\n// normalise CRLF before matching edit_file anchors\n"
               "export const normalizeEol = (s: string): string => s.replace(/\\r\\n/g, \"\\n\");\n",
               "src/commands/create.ts": "\n// stage-4 schematic check compares LF-normalised text\n",
               "test/draft-tools.test.ts": "\n// TODO: CRLF fixture for edit_file\n",
               "test/safety.test.ts": "\n// TODO: CRLF fixture for schematic check\n"}},
    {"branch": "crlf-normalize", "title": "normalize line endings when editing files",
     "body": "Small fix: strip \\r before applying edits.\n\nfixes #{issue}",
     "edits": {"src/agent/filetools.ts": "\n// strip CR so anchors match on Windows checkouts\n"
               "export const stripCr = (s: string): string => s.split(\"\\r\").join(\"\");\n"}},
    {"branch": "loop-turn-limit-log", "title": "log when the agent loop hits its turn limit",
     "body": "Adds a log line when the loop stops at max turns.",
     "edits": {"src/agent/loop.ts": "\n// log when the loop stops at the max-turn limit\n"}},
]


def sh(*cmd, cwd=None, input=None, check=True) -> str:
    r = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise RuntimeError(f"{cmd[:3]} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def load_history(graph) -> None:
    raw = json.loads((DATA / f"{SRC.replace('/', '_')}.json").read_text(encoding="utf-8"))
    print("history ->", DEMO, load(graph, remap_raw(raw, DEMO)))


def repo_exists() -> bool:
    return subprocess.run(["gh", "repo", "view", DEMO], capture_output=True).returncode == 0


def git(work, *a):
    return sh("git", "-c", "user.name=adhavan18", "-c", "user.email=sk.tamiladhavan@gmail.com", *a, cwd=work)


def create_repo(work: Path) -> None:
    src = work / "src_clone"
    sh("git", "clone", "--depth", "1", f"https://github.com/{SRC}.git", str(src))
    sha = sh("git", "rev-parse", "HEAD", cwd=src)
    dst = work / "demo"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", ".github"))
    if (dst / "README.md").exists():
        (dst / "README.md").rename(dst / "README.copperhead.md")
    assert (dst / "LICENSE").exists(), "upstream LICENSE missing"
    (dst / "README.md").write_text(README.replace("{sha}", sha), encoding="utf-8")
    git(dst, "init", "-b", "main")
    git(dst, "add", "-A")
    git(dst, "commit", "-q", "-m", f"snapshot of {SRC} at {sha[:12]} for the PR Radar demo")
    sh("gh", "repo", "create", DEMO, "--public", "--source", str(dst), "--push",
       "--description", f"Demo copy of {SRC} for PR Radar (FalkorDB) triage")


def ensure_issue() -> int:
    found = json.loads(sh("gh", "issue", "list", "--repo", DEMO, "--state", "all", "--search", ISSUE_TITLE,
                          "--json", "number,title"))
    for i in found:
        if i["title"] == ISSUE_TITLE:
            return i["number"]
    url = sh("gh", "issue", "create", "--repo", DEMO, "--title", ISSUE_TITLE, "--body",
             "Files checked out with CRLF endings fail edit_file anchor matching and the stage-4 "
             "schematic check. (demo issue)")
    return int(url.rstrip("/").rsplit("/", 1)[1])


def ensure_pr(work: Path, spec: dict, issue: int) -> int:
    found = json.loads(sh("gh", "pr", "list", "--repo", DEMO, "--state", "all", "--head", spec["branch"],
                          "--json", "number"))
    if found:
        return found[0]["number"]
    clone = work / "pushclone"
    if not clone.exists():
        for attempt in range(4):  # flaky networks drop large clones
            try:
                sh("gh", "repo", "clone", DEMO, str(clone), "--", "--depth", "1")
                break
            except RuntimeError:
                shutil.rmtree(clone, ignore_errors=True)
                if attempt == 3:
                    raise
    git(clone, "checkout", "-q", "main")
    git(clone, "checkout", "-q", "-B", spec["branch"])
    for path, add in spec["edits"].items():
        with open(clone / path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(add)
    git(clone, "commit", "-q", "-am", spec["title"])
    git(clone, "push", "-q", "-u", "origin", spec["branch"], "--force")
    url = sh("gh", "pr", "create", "--repo", DEMO, "--head", spec["branch"], "--base", "main",
             "--title", spec["title"], "--body", spec["body"].format(issue=issue))
    return int(url.rstrip("/").rsplit("/", 1)[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-post", action="store_true")
    a = ap.parse_args()
    from prgraph.db import connect
    g = connect()
    load_history(g)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        if not repo_exists():
            create_repo(work)
        issue = ensure_issue()
        nums = [ensure_pr(work, s, issue) for s in PRS]
    print(f"issue #{issue}, PRs {nums}")
    sys.stdout.reconfigure(encoding="utf-8")
    for n in nums:
        res = triage(DEMO, n, post=not a.no_post, graph=g, memory=not a.no_post)
        print(f"\n===== #{n} labels={res['labels']}\n{res['comment']}")


if __name__ == "__main__":
    main()
