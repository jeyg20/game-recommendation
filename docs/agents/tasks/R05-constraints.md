# R05: Length and price limits

**Branch:** `feat/r05-constraints` off `ai-created` · **Wave:** 2 · **Needs:** F04 and R04 merged

## Goal
"Something under 10 hours", "nothing over $20" become real filters instead of notes the ranking ignores.

## Today
- `note_request(notes=[...])` stores free text like "short sessions"; nothing in ranking reads it.
- After F04, picks carry `price_usd` and `discount_percent`.
- HLTB times exist only for owned games (`library.add_hltb_data`, async `hltb.get_game_hltb(name)`).

## Design
- `RequestDraft` and `recommend_games` / `note_request` gain `max_hours: float | None` (HLTB main story) and
  `max_price_usd: float | None`. The draft value applies unless the call passes its own (same as other steers).
- Price: filter on `price_usd` (current price, after discount) before `limit`.
- Length: only after scoring, look up HLTB for the best `LENGTH_LOOKUP = 3 * limit` picks that pass the other
  rules, cached by appid in `data/candidates/hltb_candidates.json` (path in `config.py`, `{appid: hours | null,
  fetched_at}`), refetched after 30 days. Games with no HLTB time are kept but flagged `length_unknown: true`, and
  ranked after the ones that are known to fit. Run the async lookups the way `library.load_library` does
  (`asyncio.run`); the tools run on worker threads in MCP, so that's safe.
- Picks gain `main_story_hours` when looked up. `applied` reports the limits.
- Both prompts: map "short", "under N hours", "cheap", "under $N" to these fields, and keep soft wishes as notes.

## Touches
`tools.RequestDraft`, `tools.note_request`, `tools.recommend_games`, `config.py`, a new `src/constraints.py` (the
filters and the HLTB cache), `ui.table` (hours column when present), both prompts, README, tests.

## Acceptance
- [ ] Every pick respects `max_price_usd`.
- [ ] Known lengths respect `max_hours`; unknown ones are flagged and ranked after.
- [ ] A second call with the same limit makes no HLTB requests.
- [ ] No limits set: output identical to before.

## Proof
Unit tests with a fake HLTB lookup. Live: `scripts/mcp_smoke.py recommend_games '{"limit": 5, "max_hours": 15,
"max_price_usd": 20}'`, then the same call again showing no new lookups.

## Out of scope
Steam Deck support (no data source in the current APIs), multiplayer/solo (boost/avoid tags already do that).
