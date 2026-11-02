# R01: Variety in the top picks

**Branch:** `feat/r01-diversity` off `ai-created` · **Wave:** 2 · **Needs:** T01

## Goal
The top 10 shouldn't be ten variations of one sub-genre (e.g. ten open-world survival-craft games). Spread the
picks while keeping the best fit first. This is in the README's "Open ideas".

## Today
`recommend.recommend` → `ranked()` scores finalists and sorts by cosine only. Picks that share most tags score
almost the same, so they cluster.

## Design
Maximal marginal relevance after scoring, in `recommend.py`:
`next = argmax over remaining of  λ * score(c) - (1 - λ) * max(sim(c, s) for s in selected)`, where `sim` is the
cosine between the two candidates' tag vectors (same df table). `DIVERSITY_LAMBDA = 0.75` as a module constant.
Re-rank only the first `DIVERSITY_POOL = 30` picks; the tail keeps its order. Keep each pick's `score` (fit to the
profile) unchanged: only the order changes. Apply it to recommendations, not the backlog. You will need the
candidates' tag vectors in `ranked()`; build them once, don't recompute per comparison.

## Touches
`recommend.py` only (+ tests).

## Acceptance
- [ ] With `DIVERSITY_LAMBDA = 1.0` the output is identical to today's.
- [ ] T01's diversity number (mean pairwise cosine of the top 10) goes down.
- [ ] T01's recall@25 doesn't drop by more than 1 game. If it does, report it and propose a λ instead of shipping.

## Proof
Unit test: three near-duplicates and one different game, the different one moves up. `scripts/evaluate.py
--label before` on `ai-created` and `--label after` on your branch: paste both summary lines.

## Out of scope
Changing the scores, the floor or the pools. Diversifying the backlog.
