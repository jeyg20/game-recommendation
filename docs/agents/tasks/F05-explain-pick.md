# F05: Explain one pick

**Branch:** `feat/f05-explain-pick` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
"Why Hades?" gets a faithful answer from the numbers: which tags drove the score, how much each one contributed,
and which of the player's own games put those tags in the profile.

## Today
- `recommend.matched_tags` lists the top 5 tags by `candidate[tag] * profile[tag]`, without amounts.
- The score is `cosine(profile, |profile|, candidate)`, so per-tag contributions
  `candidate[t] * profile[t] / (|profile| * |candidate|)` sum exactly to the score.
- The profile is `sum(weight * tag_vector(game))` over owned games (`tools.ensure_loaded`), so each owned game's
  share of a tag is `weight * tag_vector(game)[t]`.

## Design
- New module `src/explain.py`: `explain(candidate_tags, profile, df_table, library_games) -> Explanation` with
  `score`, `tags: [{tag, contribution, share_of_score}]` (top 8) and `because_you_played: [{name, hours,
  tags}]`: for the top 3 tags, the owned games contributing most to each.
- Tool `explain_pick(name: str, steered: bool = False)`: `find_game` → explain against the library profile. With
  `steered=True` use the session's noted draft (same steering `recommend_games` applies). Say in the docstring that
  this is the fit to the profile, not a quality judgement.

## Touches
New `src/explain.py`, the registries, both prompts (one line: "for why-questions about a pick, call
explain_pick"), README.

## Acceptance
- [ ] Contributions sum to the score (to 1e-6).
- [ ] Owned game: still explained, with `owned: true`.
- [ ] Read-only.

## Proof
Unit test of the sum and of `because_you_played` with a 3-game fake library. Live:
`scripts/mcp_smoke.py explain_pick '{"name": "Hades"}'`.

## Out of scope
Changing scoring or `matched_tags`.
