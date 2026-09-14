#Game Recomendation

This application allow you to get game recomendations by using your steam data and the llm model of your choise, it
can cross-reference a thousands of games agains a taste profile build by AI

## TODO

Checkboxes track progress — check one off only once it's actually working against real data, not just written.

### Foundation (done)

- [x] Fetch owned games + playtime via Steam Web API → cached `data/raw/steam_api_data.json`
- [x] Fetch HLTB completion time + review score per owned game → cached `data/raw/hltb_game_data_list.json`
- [x] Fetch Steam Store genres/categories per owned game, filter out non-games/utilities via is_real/is_utility →
      cached `data/raw/steamstore_data.json`

### Known issues to fix

- [ ] **Appid key type inconsistency.** `games` is keyed by `int` on a cold run but by `str` after any cache read
      (JSON object keys are always strings). SteamSpy returns `appid` as an `int`. If this isn't normalized at one
      boundary, the "subtract owned appids from candidates" step will silently fail to match anything, and
      already-owned games will leak into recommendations.
- [ ] **Liked-it ratio needs a saturation cap, not a raw division.** `playtime_hours / main_story_hours` is
      unbounded — a 400-hour game against a 15-hour main story gives a weight of ~26 and will drown every other game
      in the profile. Decide the saturation curve deliberately.
- [ ] **`data/raw/` naming has drifted.** `steamstore_data.json` filters non-games/utilities and merges in Store
      metadata — that's a transformation, not a raw passthrough. Rename that file's location rather than
      restructuring everything.
- [ ] **HLTB fetch loop isn't resumable.** Only guarded by `if not HLTB_DATA_FILE_PATH.is_file()`; a failure partway
      means refetching the whole library. Low priority at current library size (61 games, already cached).
- [ ] **`OMITTED_NAME_FRAGMENTS` is declared but never used.** Either wire it in or remove it.

### Build order

- [ ] 1. Fix appid key normalization across the cache read/write boundary (blocks everything below).
- [ ] 2. Compute the liked-it weight per owned game (ratio + saturation cap). Decide how to handle the 1 owned game
      missing HLTB data.
- [ ] 3. Fetch SteamSpy `appdetails` for all owned games → new cache file (~1 req/sec, ~47s for current library).
      This becomes the tag source — SteamSpy's community tags are already specific and vote-weighted, so no LLM tag
      extraction or manual tag conglomeration is needed. The existing Steam Store `genres` fetch stays, but only as
      the is_real/is_utility filter, not as a scoring signal.
- [ ] 4. Build the profile vector: normalize each owned game's tag votes to proportions, weight by liked-it ratio,
      sum across all owned games.
- [ ] 5. Pick top-N tags from the profile vector (start N ≈ 5-8, tune by inspecting pool size). Use tag strings
      exactly as SteamSpy returns them — hand-typed tag strings that don't exactly match SteamSpy's vocabulary return
      a near-empty result set with a 200 status, not an error.
- [ ] 6. Retrieve candidates: one `tag` request per top tag (~1 req/sec), union results, subtract owned appids
      (int-normalized).
- [ ] 7. Narrow locally (free, no extra requests): apply a shovelware floor using `positive`/`negative`/`owners`
      already present in the retrieval response, then rank by how many of the N tag pools each candidate appeared
      in. Cut to roughly 100 finalists.
- [ ] 8. Enrich finalists only: SteamSpy `appdetails` on the ~100 finalists (~100s at 1 req/sec) for full weighted
      tag vectors. Score against the profile vector — this is where real ranking happens.
- [ ] 9. Backlog pass: score zero-playtime owned games against the profile vector using data already fetched in
      step 3. No new requests. Separate output section from net-new discoveries.
- [ ] 10. LLM explanation stage for the top-N only. Give it an optional profile-text parameter (empty for now) as a
      seam for the psychological/bio profile feature planned for a later version.
- [ ] 11. Wire it all into `main.py`'s CLI flow: ranked recommendation list + separate backlog section.

### Notes on SteamSpy usage

- Rate limits: 1 req/sec for `appdetails`, `tag`, `genre`, `top100*`; 1 req/60s for `all`.
- Only `appdetails` returns `tags`/`genre`/`languages`. `tag`, `genre`, `top100*`, and `all` all return the same
  slim popularity/price schema with no thematic data — there is no bulk shortcut for tags.
- `genre` is far too coarse for candidate retrieval (`genre=Action` alone returned 36,918 games). Use `tag`, not
  `genre`, as the retrieval axis.
- Data refreshes once every 24h server-side — no reason to refetch the same appid/tag more than once a day. The
  existing cache-file-per-stage pattern already satisfies this.

### Deferred to a later version

- Psychological/bio profile text feeding into the LLM explanation step (seam left in step 10, not built yet).
