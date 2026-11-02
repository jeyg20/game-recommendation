# B01: Keep using a stale tag pool when SteamSpy returns nothing

**Branch:** `feat/b01-stale-tag-pools` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
The player's strongest tags must always feed the candidates. Right now they silently drop out.

## Today
- Found 2026-11-01: SteamSpy's `request=tag` returns `[]` (HTTP 200) for the biggest tags. These include
  `Open World`, `Survival` and `Multiplayer`, and even `request=genre&genre=Indie`. Smaller tags still work
  (`Rogue-like`: 5,442 games). It's been like this since about 2026-10-27, which is the last time those pools
  were fetched successfully.
- `recommend.fetch_tag_pools` ignores a cached pool older than `TAG_POOL_MAX_AGE_DAYS = 1`. It refetches the pool,
  gets `[]`, warns "tag pool is empty", and skips the tag. The cache still holds the 2026-10-27 pools
  (`Open World`: 6,901 games, `Multiplayer`: 11,836), but they're never used. The owner's top three profile tags
  are all affected, so every recommendation is built without its main pools.
- A failed or empty fetch is deliberately not cached. Keep that.

## Design
- When a fresh fetch fails (`None`) or comes back empty, and a cached pool exists for that tag, use the cached
  pool, however old it is. Don't rewrite the cache entry, so a later successful fetch still replaces it.
  Warn once per tag with the pool's age, e.g. `using cached 'Open World' pool from 2026-10-27 (SteamSpy returned
  nothing)`.
- Return which tags were served stale, so `recommend()` can expose them. Add `stale_pools: {tag: fetched_at}` to
  `RecommendResult`, and pass it through `tools.recommend_games` only when it isn't empty. Keep the existing
  `requirement_unenforced` meaning: a stale pool counts as fetched.
- Don't retry the empty fetch on every call within one run. Once a tag has come back empty in this process,
  use the stale pool for the rest of the process. A module-level set is enough.

## Touches
`recommend.fetch_tag_pools`, `recommend.RecommendResult` / `recommend()` return, one optional field in
`tools.recommend_games`, tests.

## Acceptance
- [ ] An empty or failed fetch with a cached pool uses the cached pool, with a warning.
- [ ] An empty fetch with no cached pool still skips the tag, as today.
- [ ] A fresh pool (under a day old) makes no request, as today.
- [ ] A later non-empty fetch overwrites the stale entry.

## Proof
Tests with a fake `get_games_by_tag` covering the four cases. Live, in your worktree:
`scripts/mcp_smoke.py recommend_games '{"limit": 5}'` showing `stale_pools` with `Open World` (if SteamSpy is
still returning empty), and `candidate_pools` now including it.

## Out of scope
Working around SteamSpy (other endpoints, scraping), and refreshing the stale pools some other way.
