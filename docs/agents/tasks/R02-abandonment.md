# R02: Abandoned games count against their tags

**Branch:** `feat/r02-abandonment` off `ai-created` · **Wave:** 2 · **Needs:** T01

## Goal
A game the player tried and dropped long ago is a signal *against* its tags, not a small vote for them. This is
in the README's "Open ideas" ("negative weight for early abandonment instead of a zero floor").

## Today
- `tag_profile.taste_weight(playtime, main_story, tags)` is always ≥ 0, so an abandoned game still adds a little.
- `tag_profile.build_profile` skips weights ≤ 0.
- `tools.is_backlog` treats "under 2 h and under 25% of the main story" as *barely started*, and those games are
  suggested again as backlog picks.
- `LibraryGame` has no last-played date, but the raw `OwnedGame` list (`tools.state["player_games"]`) has
  `rtime_last_played` (unix seconds; 0 = never).

## Design
Note (T01): the profile weighting lives in `tools.library_profile(games, df_table)`, shared by `ensure_loaded`
and `src/evaluate.py`. Change the weighting there, so the evaluation measures the real profile.

- Abandoned = played (`playtime_hours > 0`), `is_backlog` is true (barely started), and last played more than
  `ABANDON_MIN_DAYS = 120` days ago. Never-launched games (0 h) are not abandoned: no signal.
- Abandoned games get weight `-ABANDON_WEIGHT = -0.3` in `build_profile` (allow negative weights). After summing,
  drop tags whose value is ≤ 0, so the profile stays non-negative.
- Join `rtime_last_played` into the weighting in `tools.ensure_loaded` (a dict appid → last played from
  `player_games`). Keep `taste_weight`'s signature; put the abandonment rule in its own function
  (`is_abandoned(game, last_played, now)`), so R03 can reuse the join.
- Abandoned games stop being suggested as backlog picks (the player already tried and left them). Say this in the
  hand-back: it is a product change.

## Touches
`tag_profile.build_profile` (+ new `is_abandoned`), `tools.ensure_loaded`, `tools.is_backlog` callers in
`recommend_games` / `get_taste_profile` (backlog filter and count), README pipeline step 3, tests.

## Acceptance
- [ ] An abandoned game's top tags are weaker in the profile than without it.
- [ ] Never-launched games change nothing.
- [ ] T01 before/after numbers reported. Recall@25 must not drop by more than 1 game.

## Proof
Unit tests for `is_abandoned` (boundaries: 0 h, 1.9 h, 2 h, 119/121 days) and for negative weights in
`build_profile`. `scripts/evaluate.py` before/after. Live: `get_taste_profile` top tags before/after.

## Out of scope
Recency weighting (R03), achievements (F07).
