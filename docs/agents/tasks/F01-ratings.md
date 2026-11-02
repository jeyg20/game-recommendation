# F01: Ratings and "not interested"

**Branch:** `feat/f01-ratings` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
The player can react to picks, and the next recommendations learn from it.
"👍 Hades, 👎 Dead Cells, I'm not interested in Rust." Liked games pull the profile toward them, disliked ones
push it away, and dismissed games never come back.

## Today
- `tools.recommend_games` builds `excluded = owned appids | reference appids` (~line 223) and steers with
  `adjust_profile(profile, references, boost, avoid)`.
- `adjust_profile` (tag_profile.py) adds unit-length reference vectors sharing `REFERENCE_WEIGHT`; there is no
  negative direction.
- `tools.find_game(name)` resolves a name to `{appid, name, tags}` (cached per session).

## Design
- New module `src/feedback.py`, data in `data/feedback.json` (path in `config.py`):
  `Feedback = TypedDict(liked: dict[str, FeedbackGame], disliked: ..., not_interested: ...)` keyed by appid as a
  string, `FeedbackGame = {appid, name, tags, at}` (store the tags so ranking needs no request later).
- Tool `rate_games(liked: list[str] | None, disliked: list[str] | None, not_interested: list[str] | None,
  remove: list[str] | None)`: resolves names with `find_game`, saves, returns the stored lists plus names it couldn't
  find. A game is in at most one list; rating it again moves it.
- Tool `get_feedback()`: the stored lists (read-only).
- In `recommend_games`: add every `not_interested` and `disliked` appid to `excluded`. Liked games join the
  references like saved `like_games` do. Disliked games go to a new `adjust_profile(..., negatives=...)` argument
  that subtracts `DISLIKE_WEIGHT = 0.5` shared between them (unit-length, like references). Drop tags that end up
  ≤ 0. Add `feedback_applied: {liked, disliked, not_interested}` counts to the result's `applied`.
- Liked/disliked games always apply (like saved preferences); `use_saved_preferences=False` turns them off too.

## Touches
New `src/feedback.py`, `config.py`, `tools.recommend_games` (excluded + references + the call), `tag_profile.
adjust_profile` (new keyword, default empty: other callers unchanged), the registries and both prompts (one line:
"when the player reacts to picks, call rate_games"), README.

## Acceptance
- [ ] A game marked not interested or disliked never appears in recommendations.
- [ ] Liking a game moves the steered profile's top tags toward its tags; disliking moves them away.
- [ ] Unknown names are reported, not saved.
- [ ] Without any feedback, `recommend_games` output is unchanged.

## Proof
Tests for `adjust_profile` negatives and for the exclusion (fake pools). Live: `scripts/mcp_smoke.py` with
`rate_games` (not_interested on a game from the current top 10) then `recommend_games`, showing it is gone. Clean up
the test entry in your worktree's `data/feedback.json`.

## Out of scope
Using feedback in the backlog, learning weights, UI changes beyond the tool.
