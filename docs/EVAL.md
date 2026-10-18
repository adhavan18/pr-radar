# backtest: are pr radar's answers right?

reproduce (needs the FalkorDB instance from `.env`, about 8 minutes, both repos run in parallel):

```
python -m prgraph.eval
```

raw numbers: [`eval_results.json`](eval_results.json). metric code is unit tested offline in `tests/test_eval.py`.

## method: replay history, never peek

for each repo, the cached PRs (200 per repo, `data/*.json`) are replayed in `createdAt` order into a scratch graph (`prgraph_eval_<repo>`), using the same `ingest.to_rows` rows and `ingest.LOAD` Cypher as production. when PR p is evaluated:

- the graph only holds PRs created before p (plus p itself),
- review edges are only present if the review was submitted before p was created, so p's own reviews are never visible,
- `CO_CHANGED` weights are recomputed from the PRs seen so far.

then for each p the real queries from `prgraph/queries.py` run unchanged:

1. **impact (leave-one-out):** p is added with only its first file. `impact` (k=10) predicts co-changed files; we measure recall of p's other files. merged PRs with 2..50 files.
2. **reviewers:** p's remaining files and issue links are added, `reviewers` (k=10) runs. truth = humans (not bots, not the author) who actually reviewed p. only PRs with at least one such reviewer are scored.
3. **duplicates:** the three dup queries run for p; since only earlier PRs exist, each pair is found once, when its later PR appears.

baselines for reviewers, both time-respecting:
- **most active**: humans ranked by number of PRs reviewed before p (author excluded),
- **random**: shuffled list of everyone who had reviewed anything before p.

## results

### reviewer recommendation

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

ruff (astral-sh/ruff, 300 cached PRs, run separately on 2026-10-17 with the same code, 286s): the graph query edges the baseline on hit@1 (0.267 vs 0.257) but trails it clearly on hit@3 (0.495 vs 0.638) and MRR. same pattern as the other two repos, on a third, larger one.

after the redesign (see "before vs after" below) the graph query **ties the most-active baseline on hit@1 on both repos and is close on MRR, but still trails it on hit@3** (sqlfluff 0.583 vs 0.635, copperhead 0.421 vs 0.579; on copperhead that is 3 PRs out of 19). it is far ahead of the old query and of random. coverage changed because candidates are now only people who have reviewed in the repo: copperhead drops to 0.68 because early PRs have no visible human review yet.

in only 58% (copperhead) / 71% (sqlfluff) of PRs had any true reviewer reviewed anything earlier in the window, which caps every method.

### impact (co-changed files, leave-one-out)

| repo | merged PRs scored | avg files to find | coverage (any prediction) | recall@5 | recall@10 |
|---|---|---|---|---|---|
| copperhead | 56 | 11.4 | 0.54 | 0.067 | 0.118 |
| sqlfluff | 72 | 4.3 | 0.21 | 0.022 | 0.022 |
| ruff | 90 | 4.5 | 0.34 | 0.152 | 0.174 |

weak. `CO_CHANGED` needs a pair to co-occur in at least 2 earlier PRs, which is rare in a 200-PR window, so most first files have no co-change edges at all (coverage 21% on sqlfluff, which has many dialect files touched once). when it does answer on copperhead it finds about 1 in 9 of the remaining files.

### duplicate detection

ground truth: (a) pairs of PRs that fix or reference the same issue, (b) explicit "duplicate of / superseded by / supersedes / replaces #n" in PR bodies. comments are not cached, so (b) is tiny: 1 pair in copperhead (#311/#336), 0 in sqlfluff.

against shared-issue truth (copperhead 382 pairs, sqlfluff 35):

| query | copperhead p | r | f1 | sqlfluff p | r | f1 |
|---|---|---|---|---|---|---|
| `dup_shared_issue` (FIXES only, <= 3 fixers) | 1.00 | 0.03 | 0.06 | 1.00 | 0.43 | 0.60 |
| `dup_file_overlap` | 0.08 | 0.10 | 0.09 | 0.13 | 0.46 | 0.20 |
| `dup_similar_title` | 0.93 | 0.10 | 0.18 | 0.38 | 0.51 | 0.43 |
| overlap OR title | 0.13 | 0.18 | 0.15 | 0.16 | 0.71 | 0.27 |
| overlap AND title | 1.00 | 0.02 | 0.03 | 0.41 | 0.26 | 0.32 |
| all three (any) | 0.14 | 0.19 | 0.16 | 0.18 | 0.77 | 0.29 |

ruff (29 shared-issue pairs, p / r / f1): `dup_shared_issue` 1.00 / 0.48 / 0.65, `dup_file_overlap` 0.02 / 0.62 / 0.04, `dup_similar_title` 0.06 / 0.34 / 0.10, overlap AND title 0.13 / 0.31 / 0.18. file overlap is very noisy on ruff (831 predicted pairs): many PRs share mdtest/snapshot files.

the truth set still counts any shared issue link (FIXES or REFERENCES), but `dup_shared_issue` now only follows FIXES and skips issues fixed by more than 3 PRs (tracking issues). so it is no longer circular: it keeps precision 1.0 and gives up most of copperhead's 382 "pairs", which came from a few tracking issues referenced by many PRs and are related work, not duplicates. the single explicit supersede pair (#311/#336) is no longer found by any query. title similarity is still the best issue-independent signal; file overlap alone is noisy. `dup_similar_title` now returns PRs only (it was already PR-only in the replay, so its numbers are unchanged).

## before vs after (eval-driven changes, 2026-10-17)

### reviewers

before: `files*3 + sum(REVIEWED=2, AUTHORED=1)` over people who touched the same files.
after: candidates are humans with a `REVIEWED` edge in the repo;
`score = 3*log(1 + #PRs reviewed) + sum over this PR's files they reviewed of 1/log(1 + #PRs touching the file)`.
the first term is the REVIEWED degree (a graph prior), the second is file expertise with IDF so hub files (CHANGELOG, fixtures) count little. evidence: up to 3 `PR-TOUCHES-File<-TOUCHES-PR<-REVIEWED-Person` paths plus one `Person-REVIEWED->PR` path.

| repo | method | hit@1 | hit@3 | MRR |
|---|---|---|---|---|
| copperhead (19) | before | 0.158 | 0.316 | 0.254 |
| | **after** | **0.368** | 0.421 | 0.423 |
| | most active baseline | 0.368 | 0.579 | 0.456 |
| sqlfluff (96) | before | 0.125 | 0.198 | 0.164 |
| | **after** | **0.260** | 0.583 | 0.432 |
| | most active baseline | 0.260 | 0.635 | 0.438 |

all variants tried (same replay, scored side by side via `backtest_repo(..., variants=...)`). `pw` = prior weight, IDF on unless noted, REVIEWED-only unless noted:

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

honest reading: the review-volume prior does almost all the work. file expertise on top of it barely moves the numbers in a 200-PR window, and no variant beat the baseline on hit@3 on both repos. even "prior only" computed in the graph lands slightly below the Python baseline on hit@3; per-PR inspection showed the difference is tie ordering among equally active reviewers (the Python counter breaks ties by insertion order), not a different signal. recency helped hit@1 on one repo and hurt on the other, so it was not adopted. pw=3 was chosen as the simplest setting at the top of the flat region, not the best single cell.

### duplicates

`dup_shared_issue`: FIXES only, issues with more than `$max_issue_prs` (3) fixing PRs skipped. copperhead goes from 382 predicted pairs to 12 (all correct against the shared-issue truth), sqlfluff from 35 to 15. `dup_similar_title`: PRs only.

### bridges

new derived `COLLAB` edges (person-person, weight = #PRs where one authored and the other reviewed, bots excluded, per repo). FalkorDB's `algo.betweenness` has no undirected option (`direction`/`relDirection` are rejected as unknown keys, and a one-direction path a->b<-c gives b a score of 0), so each pair is stored in both directions. `bridges` is now real betweenness over COLLAB. live top 5 for sqlfluff: rubytobi 463, adhavan18 409, keraion 400, peterbud 164, alanmcruickshank 124. caveat: betweenness runs over the whole graph, so a person active in both ingested repos (adhavan18) scores as a cross-repo bridge.

## limitations

- 200 PRs per repo, a few weeks of history; the cache has no comments, so explicit duplicate truth is nearly empty.
- "who reviewed" is a proxy for "who should have reviewed"; a good suggestion that was not used counts as a miss.
- reviews with no `submittedAt` are treated as never visible (conservative).
- issue nodes are not loaded in the replay (only PRs), so `dup_similar_title` here is PR-to-PR only.
- single run; random baseline uses seed 0.
