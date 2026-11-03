# R06: Pick finalists by fit, not by pool hits

**Branch:** `feat/r06-finalists-by-fit` off `ai-created` · **Wave:** 5 · **Needs:** C01

## Goal
Good fits must reach scoring. Today half of the player's favourites can't be found again even though they sit in the
candidate pools and pass the floor: they're cut before their fit is ever computed.

## Today
`recommend.pick_finalists` unions the 8 tag pools (thousands of games), drops owned and shovelware, then keeps
`FINALISTS = 100` ranked by **how many pools a game is in** (ties: review count). Only those 100 get tags
(`enrich_tags`, cached corpus or 1 request/s) and a cosine score. A game in 2 pools that fits perfectly loses to a
generic game in 5. The same cut is used by `recommend.wildcards` and `tools`' required-tag path (`allowed_appids`).

## Design
- Most candidates already have tags in the background corpus (~4.4k `ok` records). Score **every** candidate that has
  a corpus record by cosine against the profile. That costs no requests: load the corpus once per call, build tag
  vectors, score.
- Finalists = the best `FINALISTS` by that score, **plus** up to `UNKNOWN_FINALISTS = 30` candidates without a corpus
  record, picked as today (pool hits, then reviews). Only those get fetched by `enrich_tags`, so requests stay at or
  below today's.
- Keep the floor, exclusions, `allowed_appids` and the `recommend()` return shape unchanged. MMR (R01), wildcards
  (R04) and limits (R05) run after this and need no change, but check them.
- Watch the cost: scoring a few thousand vectors per call must stay well under a second. Build vectors once; reuse
  `df_table`. Measure it and report the time.
- If the evaluation shows a better `FINALISTS` / `UNKNOWN_FINALISTS` split, propose it with numbers. Don't sweep
  blindly.

## Touches
`recommend.py` (`pick_finalists`, its callers), maybe a small corpus loader helper, tests, README pipeline step 6.
Not `src/evaluate.py` (T02 edits it in parallel).

## Acceptance
- [ ] A candidate in only one pool with a near-perfect fit becomes a finalist ahead of a poorly fitting 5-pool one.
- [ ] Candidates missing from the corpus still get considered (pool-hit order) and fetched, with no more fetches
      than today.
- [ ] T01 favourites numbers before and after, the same day. recall@25 and never-retrieved should improve. If they
      don't, report it and stop: the premise is wrong.
- [ ] Time per `recommend_games` call reported before and after.

## Proof
Unit tests with a fake corpus and pools. `scripts/evaluate.py --label "before R06"` / `"after R06"`. A live
`scripts/mcp_smoke.py recommend_games '{"limit": 10}'` showing the picks before/after.

## Out of scope
The pool tags themselves, scoring maths, MMR and wildcards settings, the evaluation code.
