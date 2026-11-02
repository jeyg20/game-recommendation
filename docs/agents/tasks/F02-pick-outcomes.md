# F02: Did past picks work?

**Branch:** `feat/f02-pick-outcomes` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
A real measure of how good the recommendations are: of everything ever recommended, what did the player buy, and
how much did they play it? "How good have your recommendations been?"

## Today
- Every `recommend_games` call is logged with its full result (`history.load_session`, `list_sessions`), and every
  recommended game was unowned at the time (owned games are excluded).
- `tools.ensure_loaded()` gives the current library with `playtime_hours` per appid.

## Design
- New module `src/outcomes.py` with `pick_outcomes() -> Outcomes`: walk every session's `recommend_games` calls,
  collect each recommended appid with the first time it was recommended, its rank and score, then join with the
  current library: `bought` (now owned), `hours` (playtime since; total playtime is fine, it was 0 when picked).
- Tool `get_pick_outcomes(limit: int = 20)`: summary (`recommended`, `bought`, `played_over_2h`, hit rate) and the
  bought games, best-played first. Note in the result that it reflects the library as of the last refresh.
- Backlog picks: separate counts (`backlog_recommended`, `backlog_started`: playtime went above the logged
  `playtime_hours`).

## Touches
New `src/outcomes.py`, the registries, both prompts (one line), README. Nothing in ranking.

## Acceptance
- [ ] Counts each game once, even when recommended in many sessions.
- [ ] Works with no sessions (empty summary, no error).
- [ ] Read-only: writes nothing.

## Proof
Tests with two fake session logs in `tmp_path` and a fake library. Live: `scripts/mcp_smoke.py get_pick_outcomes
'{}'` against the real logs.

## Out of scope
Refreshing the library automatically; feeding outcomes back into ranking.
