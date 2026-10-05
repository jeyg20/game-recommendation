# Game Recommendation

Get game recommendations from your Steam library using Google's Gemini. Cross-references thousands of games
against a taste profile built from your own playtime.

## Usage

```sh
python -m src.tag_profile   # once: build the Steam-wide tag corpus (asks before paging SteamSpy, ~1h)
python main.py              # library -> taste profile -> recommendations (+ Gemini explanation)
python main.py --no-llm --top-tags 6
```

Needs `STEAM_API_KEY`, `STEAM_ID` and (optionally) `GEMINI_API` in `.env`. Run from the repo root.

## Pipeline

1. **Library** — owned games + playtime (Steam Web API), tags/genre (SteamSpy `appdetails`, non-games dropped by
   `is_excluded`), main-story time (HLTB). Cached under `data/raw/`.
2. **Corpus / idf** — SteamSpy tags for ~4k popular games (`data/corpus/`), turned into a smoothed idf table so
   generic tags (Action, Singleplayer) weigh less than distinctive ones.
3. **Taste weight** per owned game — mean of `completion = min(playtime / main_story, 1)` and
   `volume = 1 - exp(-playtime / 40)`; volume only when there's no main story or the game is multiplayer/idle.
4. **Profile vector** — sum of `weight * vote_share * idf` over all owned games' tags. The whole dict is the vector.
5. **Candidates** — one SteamSpy `tag` request per top-N profile tag (cached for a day in `data/candidates/`),
   union, minus every owned appid.
6. **Narrow** — shovelware floor (≥500 reviews, ≥75% positive), rank by number of tag pools hit, keep 100.
7. **Score** — finalists' full tag vectors (reused from the corpus, missing ones fetched and appended to it), cosine
   against the profile with the same idf table.
8. **Backlog** — zero-playtime owned games scored the same way, as a separate section.
9. **Explain** — Gemini writes a short why-it-fits per pick; `profile_text` is the seam for a bio profile later.

Results are printed and saved to `data/output/recommendations.json`.

## Open ideas

- Negative weight for early abandonment instead of a zero floor.
- Diversity: the top results can all come from one dominant sub-genre; an MMR-style re-rank would spread them out.
- `reclassify_cache` is still a stub: re-run `is_excluded` over the corpus after the exclusion rules change.

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
