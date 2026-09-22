# Game Recommendation

Get game recommendations from your Steam library using Google's Gemini. Cross-references thousands of games
against a taste profile built from your own playtime.

## Done

- [x] Fetch owned games + playtime via Steam Web API → `data/raw/steam_api_data.json` (61 games)
- [x] Fetch HLTB main-story time + review score per owned game → `data/raw/hltb_game_data_list.json`
- [x] Migrate the genre/tag source from the Steam Store API to SteamSpy `appdetails`
- [x] Fetch SteamSpy `appdetails` per owned game and filter out utilities by genre → `data/raw/steamspy_data.json`
      (implemented in `main.py`, not yet run clean — cache was deleted as stale)

## Known issues

- [ ] **Appid key type inconsistency.** `games` is keyed by `int` on a cold run but by `str` after any cache read
      (JSON object keys are always strings). SteamSpy returns `appid` as an `int`. If this isn't normalized at one
      boundary, the "subtract owned appids from candidates" step will silently fail to match anything and
      already-owned games will leak into recommendations.
- [ ] **Liked-it ratio needs a saturation cap, not a raw division.** `playtime_hours / main_story_hours` is
      unbounded — a 400-hour game against a 15-hour main story gives ~26 and will drown every other game in the
      profile. Decide the saturation curve deliberately.
- [ ] **`games_without_tags` never populates.** The guard tests `type(steamspy_data) is list` — but the response is
      always a dict. It's `steamspy_data["tags"]` that comes back as an empty list when a game has no tags.
- [ ] **`steamspy_tags_record` is never read.** It accumulates correctly now, but nothing persists or uses it after
      the loop.
- [ ] **`OMITTED_NAME_FRAGMENTS` is declared but never used.** 14 of 61 owned entries have no HLTB match and most
      aren't games (Wallpaper Engine, XSOverlay, test servers, playtests). Wiring this in clears most of them.
- [ ] **`hltb_game_data_list.json` is stale.** Written before the SteamSpy migration — its entries still carry
      `categories`/`genres` from the Steam Store era. Delete it so the HLTB stage refetches against the filtered set.
- [ ] **`src/steam_store.py` is dead code.** Nothing has imported it since the SteamSpy migration.
- [ ] **`ai_agent` is imported in `main.py` but never called.**
- [ ] **`data/raw/` naming has drifted.** These files are filtered and merged, not raw passthroughs.

## Build order

- [ ] 1. Fix appid key normalization across the cache read/write boundary (blocks everything below).
- [ ] 2. Compute the liked-it weight per owned game (ratio + saturation cap). Decide how to handle owned games
      missing HLTB data once the junk entries are filtered out.
- [ ] 3. Build the profile vector: normalize each owned game's tag votes to proportions, weight by liked-it ratio,
      sum across all owned games.
- [ ] 4. Pick top-N tags from the profile vector (start N ≈ 5-8, tune by inspecting pool size). Use tag strings
      exactly as SteamSpy returns them.
- [ ] 5. Retrieve candidates: one `tag` request per top tag (~1 req/sec), union results, subtract owned appids.
- [ ] 6. Narrow locally (no extra requests): apply a shovelware floor using `positive`/`negative`/`owners` already
      present in the retrieval response, then rank by how many of the N tag pools each candidate appeared in. Cut to
      roughly 100 finalists.
- [ ] 7. Enrich finalists only: SteamSpy `appdetails` on the ~100 finalists (~100s at 1 req/sec) for full weighted
      tag vectors. Score against the profile vector — this is where real ranking happens.
- [ ] 8. Backlog pass: score the 13 zero-playtime owned games against the profile vector using data already
      fetched. No new requests. Separate output section from net-new discoveries.
- [ ] 9. LLM explanation stage for the top-N only. Give it an optional profile-text parameter (empty for now) as a
      seam for a psychological/bio profile in a later version.
- [ ] 10. Wire it into `main.py`'s CLI flow: ranked recommendation list + separate backlog section.

## Notes on SteamSpy usage

- Rate limits: 1 req/sec for `appdetails`, `tag`, `genre`, `top100*`; 1 req/60s for `all`.
- Only `appdetails` returns `tags`/`genre`/`languages`. `tag`, `genre`, `top100*` and `all` return the same slim
  popularity/price schema with no thematic data — there is no bulk shortcut for tags.
- `tags` comes back as a `dict[str, int]` of tag → votes, but as an empty **list** when a game has no tag data.
- Hand-typed tag strings that don't exactly match SteamSpy's vocabulary return a near-empty result set with a
  200 status, not an error.
- `genre` is far too coarse for candidate retrieval (`genre=Action` alone returned 36,918 games). Use `tag` as the
  retrieval axis.
- Data refreshes once every 24h server-side — no reason to refetch the same appid/tag more than once a day.
