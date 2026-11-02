# R04: Wildcard picks outside the bubble

**Branch:** `feat/r04-wildcards` off `ai-created` · **Wave:** 2 · **Needs:** R02 merged

## Goal
A few picks the player wouldn't find from their usual tags, but that still fit: "surprise me".

## Today
`tools.recommend_games` pulls candidate pools only for `top_tags(taste, 8)`, so every candidate comes from the
player's strongest tags.

## Design
- Adjacent tags: profile tags ranked `WILDCARD_TAG_RANKS = range(9, 21)` (just outside the 8 pool tags), skipping
  generic ones (idf below the median of the df table). Take the first `WILDCARD_POOLS = 3`.
- Fetch their pools (`fetch_tag_pools`, cached, 1 req/s), drop owned/excluded games and anything already in the
  main recommendations, keep only games whose strongest tags (top 5) **don't** include any of the 8 pool tags,
  score by cosine, return the top `WILDCARD_PICKS = 3`.
- `recommend_games(include_wildcards: bool = True)` adds `wildcards: [...]` (same shape as recommendations, plus
  `via_tag`). Respects avoid tags and the shovelware floor. Doesn't apply the boost-tag requirement (a wildcard is
  by definition outside it), and skip wildcards entirely when boost tags were required.
- Render it as a third table in `ui.recommendation_tables`.

## Touches
`recommend.py` (a new `wildcards(...)` function reusing `fetch_tag_pools`, `pick_finalists`, `enrich_tags`,
`score`), `tools.recommend_games` (call + result field), `ui.recommendation_tables`, both prompts (one line: point
out a wildcard when the player wants something new), README, tests.

## Acceptance
- [ ] No wildcard is owned, already in the main picks, or carries a pool tag in its top 5.
- [ ] No wildcards when boost tags are required.
- [ ] Main recommendations are unchanged (`include_wildcards=False` gives identical output).

## Proof
Unit test with fake pools. Live: `scripts/mcp_smoke.py recommend_games '{"limit": 5}'` showing wildcards. T01
numbers before/after (should be unchanged: wildcards are a separate list).

## Out of scope
Changing how the main pools are chosen.
