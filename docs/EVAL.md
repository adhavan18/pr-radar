# Backtest: are PR Radar's answers right?

Reproduce (needs the FalkorDB instance from `.env`, about 8 minutes, both repos run in parallel):

```
python -m prgraph.eval
```

Raw numbers: [`eval_results.json`](eval_results.json). Metric code is unit tested offline in `tests/test_eval.py`.

## Method: replay history, never peek

For each repo, the cached PRs (200 per repo, `data/*.json`) are replayed in `createdAt` order into a scratch graph (`prgraph_eval_<repo>`), using the same `ingest.to_rows` rows and `ingest.LOAD` Cypher as production. When PR P is evaluated:

- the graph only holds PRs created before P (plus P itself),
- review edges are only present if the review was submitted before P was created, so P's own reviews are never visible,
- `CO_CHANGED` weights are recomputed from the PRs seen so far.

Then for each P the real queries from `prgraph/queries.py` run unchanged:

1. **impact (leave-one-out):** P is added with only its first file. `impact` (k=10) predicts co-changed files; we measure recall of P's other files. Merged PRs with 2..50 files.
2. **reviewers:** P's remaining files and issue links are added, `reviewers` (k=10) runs. Truth = humans (not bots, not the author) who actually reviewed P. Only PRs with at least one such reviewer are scored.
3. **duplicates:** the three dup queries run for P; since only earlier PRs exist, each pair is found once, when its later PR appears.

Baselines for reviewers, both time-respecting:
- **most active**: humans ranked by number of PRs reviewed before P (author excluded),
- **random**: shuffled list of everyone who had reviewed anything before P.

## Results

### Reviewer recommendation

| repo | PRs scored | coverage | method | hit@1 | hit@3 | MRR |
|---|---|---|---|---|---|---|
| copperhead | 19 | 0.95 | **graph `reviewers`** | 0.158 | 0.316 | 0.254 |
| | | | most active | **0.368** | **0.579** | **0.456** |
| | | | random | 0.158 | 0.316 | 0.272 |
| sqlfluff | 96 | 0.69 | **graph `reviewers`** | 0.125 | 0.198 | 0.164 |
| | | | most active | **0.260** | **0.635** | **0.438** |
| | | | random | 0.073 | 0.177 | 0.177 |

The graph query **loses to the "most active reviewer" baseline on both repos**, and is roughly level with random. In only 58% (copperhead) / 71% (sqlfluff) of PRs had any true reviewer reviewed anything earlier in the window, which caps every method.

Why the graph loses, and what to try:
- The score mixes **authors** and reviewers of overlapping PRs (`files*3 + weight`). Most people who touched a file are contributors, not reviewers; in repos with one or two maintainers who review everything, file overlap points at the wrong people. A version that only counts `REVIEWED` edges, or adds a prior for overall review volume, would likely beat the baseline.
- Shared files like `CHANGELOG.md`, test fixtures and lockfiles connect almost every PR, so `files` rewards whoever touched the busiest files.
- Small window: 200 PRs is a few weeks of history; file-level expertise needs more history to emerge.
- copperhead: most reviews in the cache are from bots, leaving only 19 scorable PRs (below the ~100 target; sqlfluff reaches 96).

### Impact (co-changed files, leave-one-out)

| repo | merged PRs scored | avg files to find | coverage (any prediction) | recall@5 | recall@10 |
|---|---|---|---|---|---|
| copperhead | 56 | 11.4 | 0.54 | 0.067 | 0.118 |
| sqlfluff | 72 | 4.3 | 0.21 | 0.022 | 0.022 |

Weak. `CO_CHANGED` needs a pair to co-occur in at least 2 earlier PRs, which is rare in a 200-PR window, so most first files have no co-change edges at all (coverage 21% on sqlfluff, which has many dialect files touched once). When it does answer on copperhead it finds about 1 in 9 of the remaining files.

### Duplicate detection

Ground truth: (a) pairs of PRs that fix or reference the same issue, (b) explicit "duplicate of / superseded by / supersedes / replaces #N" in PR bodies. Comments are not cached, so (b) is tiny: 1 pair in copperhead (#311/#336), 0 in sqlfluff.

Against shared-issue truth (copperhead 382 pairs, sqlfluff 35):

| query | copperhead P | R | F1 | sqlfluff P | R | F1 |
|---|---|---|---|---|---|---|
| `dup_shared_issue` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| `dup_file_overlap` | 0.08 | 0.10 | 0.09 | 0.13 | 0.46 | 0.20 |
| `dup_similar_title` | 0.93 | 0.10 | 0.18 | 0.38 | 0.51 | 0.43 |
| overlap OR title | 0.13 | 0.18 | 0.15 | 0.16 | 0.71 | 0.27 |
| overlap AND title | 1.00 | 0.02 | 0.03 | 0.41 | 0.26 | 0.32 |
| all three (any) | 0.46 | 1.00 | 0.63 | 0.22 | 1.00 | 0.36 |

**Read the first row with care: it is circular.** `dup_shared_issue` is defined by the same links as the truth set, so 1.0/1.0 only shows the ingestion and query are consistent, not that it finds real duplicates. The informative rows are the two issue-independent signals: title similarity is the better one (high precision on copperhead, about half the pairs on sqlfluff); file overlap alone is noisy. Also note copperhead's 382 "shared issue" pairs come from a few tracking issues referenced by many PRs, so many of those are related work, not true duplicates. The single explicit supersede pair was found only via the shared issue.

## Limitations

- 200 PRs per repo, a few weeks of history; the cache has no comments, so explicit duplicate truth is nearly empty.
- "Who reviewed" is a proxy for "who should have reviewed"; a good suggestion that was not used counts as a miss.
- Reviews with no `submittedAt` are treated as never visible (conservative).
- Issue nodes are not loaded in the replay (only PRs), so `dup_similar_title` here is PR-to-PR only.
- Single run; random baseline uses seed 0.
