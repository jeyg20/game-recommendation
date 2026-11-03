# T02: A second evaluation: does the profile find what you play now?

**Branch:** `feat/t02-recent-holdout` off `ai-created` · **Wave:** 5 · **Needs:** C01

## Goal
The offline evaluation should be able to judge recency and other "taste changes over time" ideas fairly, so the
coordinator can tune them by numbers instead of guessing.

## Today
`src/evaluate.py` holds out the 10 games with the highest plain `taste_weight` (all-time favourites). R03 made those
games count less on purpose when they were last played years ago, so this test is built to penalise R03: after R03,
MRR fell from 0.023 to 0.013 and 8 of 10 games were never retrieved. Five of the ten were last played 2–3 years ago.
There's no test that rewards finding what the player is into *now*.

## Design
- Keep today's set as the **favourites** set, unchanged, so old numbers stay comparable.
- Add a **recent** set: the `HOLDOUT_GAMES` findable games (same "findable" rule as `pick_held_out`: tags, in a pool
  of the full profile, passes the floor) with the most recent `rtime_last_played`, among games with real play
  (`playtime_hours >= RECENT_MIN_HOURS = 5`, not abandoned). Ties break on appid. It needs `last_played`; when that's
  missing, skip the set and say so.
- Same leave-one-out per held-out game: rebuild the profile without the game (same weighting as live), un-own it,
  record its rank.
- `Summary` gains nothing. `EvalResult` keeps `summary` / `games` for favourites (backwards compatible) and adds
  `recent_summary` / `recent_games`. Diversity is measured once.
- `scripts/evaluate.py` prints both summary lines (`favourites:` and `recent:`) and both per-game tables. Add
  `--set favourites|recent|both` (default both), so a quick tuning run can do one set.
- README: one paragraph on what each set measures and which ideas each one is fair to.

## Touches
`src/evaluate.py`, `scripts/evaluate.py`, `tests/test_evaluate.py`, README. Not `recommend.py` (R06 edits it in
parallel).

## Acceptance
- [ ] The favourites numbers are identical to today's for the same day's caches.
- [ ] The recent set is chosen by last played, excludes abandoned games and games under 5 h.
- [ ] Without `last_played`, the recent set is skipped with a message, not an error.

## Proof
Tests on a fake library. Live: `scripts/evaluate.py --label "T02"`, paste both summary lines. Then, as data for the
coordinator, the recent line with `RECENCY_FLOOR = 1.0` (recency off) and `0.5` (current), with the floor changed
temporarily and restored before you stage. Don't commit the change.

## Out of scope
Changing any ranking code or constants.
