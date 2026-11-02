# C01: Follow-ups from the wave 1 and 2 reviews

**Branch:** `feat/c01-cleanup` off `ai-created` · **Wave:** 4 · **Needs:** R05 merged

## Goal
Close the small gaps the coordinator noted while merging F01–F08 and R01–R05, so every tool tells the same story
about the profile and the caches don't lock data out. No new features.

## Today
1. **`explain_pick` drifts from the real ranking.**
   - `tools.noted_taste` (F05) copies `recommend_games`' steering (saved preferences + noted draft). It ignores F01's
     ratings (liked games as references, disliked ones as `adjust_profile(negatives=)`).
   - `explain.game_shares` weighs owned games with plain `taste_weight`, while the profile now comes from
     `tools.library_profile` (R02 abandonment at `-ABANDON_WEIGHT`, R03 recency, F07 achievement share). An abandoned
     game can show up under `because_you_played`, and the shares don't add up to the real profile's values.
2. **Tagless games are locked out.** `recommend.enrich_tags` saves a game SteamSpy has no tags for as `excluded`,
   and an `excluded` record is never fetched again. F03 found 13 of the owner's 19 wishlist games in that state
   (Stellar Blade, Dune: Awakening). They stay unrankable even after SteamSpy adds tags.
3. **Empty tag pools are never re-asked in a long-lived process.** `recommend._empty_this_run` (B01) lasts the whole
   process, so a Claude Desktop MCP server that runs for days never asks SteamSpy again for Open World etc.
4. **The CLI bottom bar doesn't show R05's limits.** `cli.toolbar` lists the draft's like/must have/avoid/note keys
   only, not `max_hours` / `max_price_usd`.

## Design
1. One steering function used by both `recommend_games` and `explain_pick`: extract it from `recommend_games`
   (saved preferences, draft, feedback; returns the steered profile plus what `applied` reports) and delete
   `noted_taste`. `explain_pick(steered=True)` passes nothing per-call, so it gets the noted/saved/rated steering.
   Keep `recommend_games`' output byte-identical (existing tests plus a before/after `mcp_smoke` diff).
   Give `explain` the same per-game weights `library_profile` uses: extract the weight computation (one function, or
   `library_profile` returning the weights it used) and pass it to `explain`, so `because_you_played` shares are terms
   of the real profile. An abandoned game's share is negative: leave it out of `because_you_played`.
2. Give tagless games their own status (e.g. `untagged`) and refetch it after `UNTAGGED_RETRY_DAYS = 7`, not on every
   call: refetching per call would cost a request per tagless candidate. Keep `excluded` for `is_excluded` hits
   (non-games), which never change. Records already saved as `excluded` with empty tags count as `untagged`.
   `CorpusRecord.status` gains the literal. Check every reader of `status` (`tag_profile`, `recommend`, the corpus
   build) still does the right thing.
3. Expire `_empty_this_run` entries after `EMPTY_RETRY_HOURS = 6` (store the time it came back empty).
4. Show `max hours: N` / `max $N` in `cli.toolbar` when set.

## Touches
`tools.py` (`recommend_games` steering extraction, `explain_pick`, `noted_taste` removed, `library_profile` weights),
`explain.py`, `recommend.py` (`enrich_tags`, `_empty_this_run`), `tag_profile.CorpusRecord`, `cli.toolbar`, tests.
Not `export.py` (F09 owns it right now).

## Acceptance
- [ ] `recommend_games` output unchanged for the same inputs (no limits, no ratings, and with ratings).
- [ ] `explain_pick(steered=True)` with a liked and a disliked game: its score equals the steered profile's cosine.
- [ ] No abandoned game in `because_you_played`; shares use the real weights.
- [ ] An empty-tag record older than 7 days is refetched; a newer one isn't; a non-game `excluded` one never is.
- [ ] An empty pool is re-asked after 6 hours in the same process.

## Proof
Unit tests for each item. Live: `scripts/mcp_smoke.py explain_pick '{"name": "Valheim", "steered": true}'` and
`recommend_games '{"limit": 5}'` before and after (paste a diff of the recommendations: none expected).

## Out of scope
`export.py` (price column, wildcards section: after F09), the finalist cut, new tools.
