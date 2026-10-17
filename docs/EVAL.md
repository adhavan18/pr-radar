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
| copperhead | 19 | 0.68 | **graph `reviewers`** | **0.368** | 0.421 | 0.423 |
| | | | most active | **0.368** | **0.579** | **0.456** |
| | | | random | 0.158 | 0.316 | 0.272 |
| sqlfluff | 96 | 0.99 | **graph `reviewers`** | **0.260** | 0.583 | 0.432 |
| | | | most active | **0.260** | **0.635** | **0.438** |
| | | | random | 0.073 | 0.177 | 0.177 |
| ruff (300 PRs) | 105 | 0.98 | **graph `reviewers`** | **0.267** | 0.495 | 0.440 |
| | | | most active | 0.257 | **0.638** | **0.464** |
| | | | random | 0.076 | 0.210 | 0.232 |

ruff (astral-sh/ruff, 300 cached PRs, run separately on 2026-10-17 with the same code, 286s): the graph query edges the baseline on hit@1 (0.267 vs 0.257) but trails it clearly on hit@3 (0.495 vs 0.638) and MRR. Same pattern as the other two repos, on a third, larger one.

After the redesign (see "Before vs after" below) the graph query **ties the most-active baseline on hit@1 on both repos and is close on MRR, but still trails it on hit@3** (sqlfluff 0.583 vs 0.635, copperhead 0.421 vs 0.579; on copperhead that is 3 PRs out of 19). It is far ahead of the old query and of random. Coverage changed because candidates are now only people who have reviewed in the repo: copperhead drops to 0.68 because early PRs have no visible human review yet.

In only 58% (copperhead) / 71% (sqlfluff) of PRs had any true reviewer reviewed anything earlier in the window, which caps every method.

### Impact (co-changed files, leave-one-out)

| repo | merged PRs scored | avg files to find | coverage (any prediction) | recall@5 | recall@10 |
|---|---|---|---|---|---|
| copperhead | 56 | 11.4 | 0.54 | 0.067 | 0.118 |
| sqlfluff | 72 | 4.3 | 0.21 | 0.022 | 0.022 |
| ruff | 90 | 4.5 | 0.34 | 0.152 | 0.174 |

Weak. `CO_CHANGED` needs a pair to co-occur in at least 2 earlier PRs, which is rare in a 200-PR window, so most first files have no co-change edges at all (coverage 21% on sqlfluff, which has many dialect files touched once). When it does answer on copperhead it finds about 1 in 9 of the remaining files.

### Duplicate detection

Ground truth: (a) pairs of PRs that fix or reference the same issue, (b) explicit "duplicate of / superseded by / supersedes / replaces #N" in PR bodies. Comments are not cached, so (b) is tiny: 1 pair in copperhead (#311/#336), 0 in sqlfluff.

Against shared-issue truth (copperhead 382 pairs, sqlfluff 35):

| query | copperhead P | R | F1 | sqlfluff P | R | F1 |
|---|---|---|---|---|---|---|
| `dup_shared_issue` (FIXES only, <= 3 fixers) | 1.00 | 0.03 | 0.06 | 1.00 | 0.43 | 0.60 |
| `dup_file_overlap` | 0.08 | 0.10 | 0.09 | 0.13 | 0.46 | 0.20 |
| `dup_similar_title` | 0.93 | 0.10 | 0.18 | 0.38 | 0.51 | 0.43 |
| overlap OR title | 0.13 | 0.18 | 0.15 | 0.16 | 0.71 | 0.27 |
| overlap AND title | 1.00 | 0.02 | 0.03 | 0.41 | 0.26 | 0.32 |
| all three (any) | 0.14 | 0.19 | 0.16 | 0.18 | 0.77 | 0.29 |

ruff (29 shared-issue pairs, P / R / F1): `dup_shared_issue` 1.00 / 0.48 / 0.65, `dup_file_overlap` 0.02 / 0.62 / 0.04, `dup_similar_title` 0.06 / 0.34 / 0.10, overlap AND title 0.13 / 0.31 / 0.18. File overlap is very noisy on ruff (831 predicted pairs): many PRs share mdtest/snapshot files.

The truth set still counts any shared issue link (FIXES or REFERENCES), but `dup_shared_issue` now only follows FIXES and skips issues fixed by more than 3 PRs (tracking issues). So it is no longer circular: it keeps precision 1.0 and gives up most of copperhead's 382 "pairs", which came from a few tracking issues referenced by many PRs and are related work, not duplicates. The single explicit supersede pair (#311/#336) is no longer found by any query. Title similarity is still the best issue-independent signal; file overlap alone is noisy. `dup_similar_title` now returns PRs only (it was already PR-only in the replay, so its numbers are unchanged).

## Before vs after (eval-driven changes, 2026-10-17)

### Reviewers

Before: `files*3 + sum(REVIEWED=2, AUTHORED=1)` over people who touched the same files.
After: candidates are humans with a `REVIEWED` edge in the repo;
`score = 3*log(1 + #PRs reviewed) + sum over this PR's files they reviewed of 1/log(1 + #PRs touching the file)`.
The first term is the REVIEWED degree (a graph prior), the second is file expertise with IDF so hub files (CHANGELOG, fixtures) count little. Evidence: up to 3 `PR-TOUCHES-File<-TOUCHES-PR<-REVIEWED-Person` paths plus one `Person-REVIEWED->PR` path.

| repo | method | hit@1 | hit@3 | MRR |
|---|---|---|---|---|
| copperhead (19) | before | 0.158 | 0.316 | 0.254 |
| | **after** | **0.368** | 0.421 | 0.423 |
| | most active baseline | 0.368 | 0.579 | 0.456 |
| sqlfluff (96) | before | 0.125 | 0.198 | 0.164 |
| | **after** | **0.260** | 0.583 | 0.432 |
| | most active baseline | 0.260 | 0.635 | 0.438 |

All variants tried (same replay, scored side by side via `backtest_repo(..., variants=...)`). `pw` = prior weight, IDF on unless noted, REVIEWED-only unless noted:

| variant | copperhead h1 / h3 / MRR | sqlfluff h1 / h3 / MRR |
|---|---|---|
| old query | 0.158 / 0.316 / 0.254 | 0.125 / 0.198 / 0.164 |
| prior only (log #PRs reviewed) | 0.368 / 0.474 / 0.436 | 0.260 / 0.583 / 0.433 |
| prior only, count review edges | 0.368 / 0.474 / 0.436 | 0.260 / 0.583 / 0.433 |
| expertise only, no prior | 0.211 / 0.263 / 0.294 | 0.219 / 0.521 / 0.394 |
| pw=0.5 | 0.211 / 0.263 / 0.304 | 0.219 / 0.542 / 0.399 |
| pw=1 | 0.211 / 0.316 / 0.318 | 0.208 / 0.583 / 0.404 |
| pw=1, no IDF | 0.211 / 0.263 / 0.304 | 0.229 / 0.562 / 0.407 |
| pw=1, + authorship at 0.3 | 0.211 / 0.316 / 0.318 | 0.208 / 0.573 / 0.403 |
| pw=2 | 0.368 / 0.421 / 0.423 | 0.250 / 0.583 / 0.425 |
| **pw=3 (chosen)** | **0.368 / 0.421 / 0.423** | **0.260 / 0.583 / 0.432** |
| pw=5 | 0.368 / 0.421 / 0.423 | 0.250 / 0.583 / 0.427 |
| pw=10 | 0.368 / 0.421 / 0.423 | 0.260 / 0.583 / 0.432 |
| pw=3, edge-count prior | 0.368 / 0.421 / 0.423 | 0.260 / 0.583 / 0.432 |
| prior only, ties broken by latest review | 0.421 / 0.421 / 0.454 | 0.260 / 0.531 / 0.425 |
| pw=3, ties by latest review | 0.368 / 0.421 / 0.432 | 0.260 / 0.542 / 0.425 |
| pw=3 + 3*log(1+reviews in last 100 PR numbers) | 0.421 / 0.421 / 0.458 | 0.240 / 0.552 / 0.418 |
| recency only (last 100) + expertise | 0.316 / 0.474 / 0.412 | 0.323 / 0.542 / 0.450 |
| pw=2 + recency (last 50) | 0.263 / 0.421 / 0.375 | 0.260 / 0.552 / 0.423 |
| prior + recency, no expertise | 0.421 / 0.421 / 0.454 | 0.260 / 0.542 / 0.428 |

Honest reading: the review-volume prior does almost all the work. File expertise on top of it barely moves the numbers in a 200-PR window, and no variant beat the baseline on hit@3 on both repos. Even "prior only" computed in the graph lands slightly below the Python baseline on hit@3; per-PR inspection showed the difference is tie ordering among equally active reviewers (the Python counter breaks ties by insertion order), not a different signal. Recency helped hit@1 on one repo and hurt on the other, so it was not adopted. pw=3 was chosen as the simplest setting at the top of the flat region, not the best single cell.

### Duplicates

`dup_shared_issue`: FIXES only, issues with more than `$max_issue_prs` (3) fixing PRs skipped. copperhead goes from 382 predicted pairs to 12 (all correct against the shared-issue truth), sqlfluff from 35 to 15. `dup_similar_title`: PRs only.

### Bridges

New derived `COLLAB` edges (Person-Person, weight = #PRs where one authored and the other reviewed, bots excluded, per repo). FalkorDB's `algo.betweenness` has no undirected option (`direction`/`relDirection` are rejected as unknown keys, and a one-direction path a->b<-c gives b a score of 0), so each pair is stored in both directions. `bridges` is now real betweenness over COLLAB. Live top 5 for sqlfluff: rubytobi 463, adhavan18 409, keraion 400, peterbud 164, alanmcruickshank 124. Caveat: betweenness runs over the whole graph, so a person active in both ingested repos (adhavan18) scores as a cross-repo bridge.

## Limitations

- 200 PRs per repo, a few weeks of history; the cache has no comments, so explicit duplicate truth is nearly empty.
- "Who reviewed" is a proxy for "who should have reviewed"; a good suggestion that was not used counts as a miss.
- Reviews with no `submittedAt` are treated as never visible (conservative).
- Issue nodes are not loaded in the replay (only PRs), so `dup_similar_title` here is PR-to-PR only.
- Single run; random baseline uses seed 0.
