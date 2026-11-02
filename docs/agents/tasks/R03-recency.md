# R03: Recent tastes count more

**Branch:** `feat/r03-recency` off `ai-created` · **Wave:** 2 · **Needs:** R02 merged

## Goal
The profile leans toward what the player plays now, not what they played years ago.

## Today
- Every owned game's weight comes from total playtime and story completion only (`taste_weight`).
- After R02, `tools.ensure_loaded` already joins `rtime_last_played` from `tools.state["player_games"]`.
  `OwnedGame` also carries `playtime_2weeks` (only present for games played in the last two weeks).

## Design
- `recency_factor(last_played, now) = RECENCY_FLOOR + (1 - RECENCY_FLOOR) * exp(-days / RECENCY_SCALE_DAYS)`
  with `RECENCY_FLOOR = 0.5`, `RECENCY_SCALE_DAYS = 365`. Never played (0) → 1.0 (weight is 0 anyway).
- Positive weights are multiplied by it; R02's negative (abandoned) weights are not.
- Games with `playtime_2weeks > 0` get factor 1.0 regardless of `rtime_last_played`.
- Put the constants and `recency_factor` in `tag_profile.py`; apply it in `ensure_loaded` next to R02's join.
- `get_taste_profile` adds `recently_played` (names with hours in the last 2 weeks), so the model can mention it.

## Touches
`tag_profile.py` (new function + constants), `tools.ensure_loaded`, `tools.get_taste_profile`, README pipeline
step 3, tests.

## Acceptance
- [ ] With `RECENCY_FLOOR = 1.0` the profile is identical to before.
- [ ] A game played last week outweighs the same playtime played five years ago.
- [ ] T01 before/after reported (note: hold-out recall may favour old favourites; explain the number, don't tune
      to it blindly).

## Proof
Unit tests for `recency_factor` (0 days, 365 days, never played, 2-week override). `scripts/evaluate.py`
before/after. Live: `get_taste_profile` before/after.

## Out of scope
Calling `GetRecentlyPlayedGames` (the owned-games data already has what's needed).
