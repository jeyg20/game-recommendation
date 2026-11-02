# F07: Achievements in the taste weight

**Branch:** `feat/f07-achievements` off `ai-created` · **Wave:** 2 · **Needs:** R03 merged

## Goal
Achievement completion is a better sign that the player finished or loved a game than playtime alone, especially
when HowLongToBeat has no main-story time.

## Today
- `taste_weight` = mean(completion, volume), or volume alone when there's no usable main story.
- `steam_api.get_player_stats` exists but is unused. Verified 2026-11-01: `ISteamUserStats/GetPlayerAchievements/
  v0001/?key&steamid&appid` returns `playerstats.achievements[]` with `achieved` 0/1 for the owner's account.
  Owned games with achievements have `has_community_visible_stats: true` in `OwnedGame`.

## Design
Note (T01): the profile weighting lives in `tools.library_profile(games, df_table)`, shared by `ensure_loaded`
and `src/evaluate.py`. Change the weighting there, so the evaluation measures the real profile.

- `steam_api.get_player_achievements(steam_id, key, appid) -> tuple[int, int] | None` (achieved, total). Games
  without achievements, or errors, → None.
- `library.py`: a cached step `data/raw/achievements.json` (`{appid: [achieved, total] | null}`), built with
  `storage.load_or_build` for owned games with `has_community_visible_stats`, refetched by `refresh_library`.
  Pace requests like the other library steps.
- `taste_weight(..., achievement_share: float | None = None)`: when present, `completion` becomes
  `max(story completion, achievement_share)`; when there's no story time, use
  `mean(volume, achievement_share)` instead of volume alone. Open-ended games keep volume only.
- Pass the share in from `tools.ensure_loaded`.

## Touches
`steam_api.py`, `library.py` (`load_library`, `refresh_library`), `config.py`, `tag_profile.taste_weight`,
`tools.ensure_loaded`, README pipeline step 3, tests.

## Acceptance
- [ ] Missing achievement data changes nothing (`achievement_share=None` gives today's weight).
- [ ] The library loads from cache without requests on the second run.
- [ ] T01 before/after reported.

## Proof
Unit tests for `taste_weight` with and without the share. Live: build the achievements cache in your worktree
(one request per eligible owned game, ~1 minute) and show `get_taste_profile` before/after.

## Out of scope
Rarity-weighted achievements, global achievement stats.
