# F04: Prices and sales

**Branch:** `feat/f04-prices` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
Picks show what they cost and whether they're on sale, and "which of my picks is on sale?" works.

## Today
- Candidate data is SteamSpy's `GameSummary` (steamspy.py) from the tag pools. `price`, `initialprice` and
  `discount` are **strings**: prices in US cents (`"2999"` = $29.99), discount in percent (`"0"`, `"50"`).
  Pools are cached for a day, so prices are up to a day old.
- `recommend.Recommendation` has `appid, name, score, positive_ratio, matched_tags`; `ranked()` inside
  `recommend()` fills `positive_ratio` from the `GameSummary`. Backlog picks have no summary.

## Design
- `Recommendation` gains `price_usd: float | None` and `discount_percent: int | None` (None for backlog). Fill
  them in `ranked()` from the `GameSummary` with a small parser (empty or bad values → None, `"0"` → 0.0).
- `tools.compact` passes both through. `ui.table` gets a price column for recommendations (e.g. `$29.99`,
  `-50% $14.99`, `free`).
- `recommend_games(on_sale_only: bool = False)`: keep only picks with `discount_percent > 0`. Applied after
  ranking, before `limit`.
- Say in the docstring that prices are US list prices from SteamSpy, up to a day old.

## Touches
`recommend.py` (`Recommendation`, `ranked`), `tools.compact`, `tools.recommend_games` (one param + filter),
`ui.table` / `recommendation_tables`, both prompts (one line), README.

## Acceptance
- [ ] Every non-backlog pick has a price (or None when SteamSpy has none).
- [ ] `on_sale_only=True` returns only discounted games, possibly fewer than `limit`.
- [ ] CLI tables and `--report` still render.

## Proof
Parser tests (`"2999"`, `"0"`, `""`, garbage). A `recommend` test with fake pools carrying prices. Live:
`scripts/mcp_smoke.py recommend_games '{"limit": 5}'` showing prices, and `uv run gamer-guide --report`.

## Out of scope
Regional prices, price history, the store API. Price limits are R05.
