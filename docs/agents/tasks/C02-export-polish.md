# C02: Export and digest polish, HLTB failures

**Branch:** `feat/c02-export-polish` off `ai-created` · **Wave:** 4 · **Needs:** F09 merged

## Goal
Make `--export` and `--digest` read like the terminal report, and stop a network hiccup from hiding a game's length
for a month.

## Today
1. **Raw price columns.** F04's `price_usd` and `discount_percent` show up in `--export` / `--digest` as two raw
   columns, "Price usd" (`34.99`) and "Discount percent" (`75`), via `export.Section.more`. The terminal report shows
   one Price cell (`ui.price()`: `$29.99`, `-50% $14.99`, `free`, `-`). R05's `main_story_hours` / `length_unknown`
   and F03's `on_wishlist` come out the same raw way ("Main story hours", "Length unknown", "On wishlist").
2. **No wildcards in `--export`.** `export.sections()` renders `recommendations` and `backlog` only. `--digest` (F09)
   has its own wildcards table.
3. **A failed HLTB search is cached as "no time" for 30 days.** `hltb.get_game_hltb` returns `None` both when nothing
   matched and when the search raised. `constraints.candidate_hours` (R05) caches either as `hours: null` for 30 days.

## Design
1. In `export.py`, render known extra fields as formatted columns:
   - **Price:** one "Price" column with the same text as `ui.price()`. Reuse it, or move the formatter somewhere both
     can import without a cycle.
   - **Story:** `12.8h`, or `?` when `length_unknown`.
   - **Wishlist:** `★` (or "yes") when `on_wishlist`, blank otherwise.
   - Unknown fields keep the current generic behaviour (their own column, dropped when `None` everywhere).
   - Escape everything as today.
2. Add a "Wildcards" section to `export.sections()` with a "Via tag" column. `digest.py` keeps its own diffed
   wildcards table. Make sure the digest uses the same formatted columns.
3. Make the HLTB miss and failure distinguishable without changing what callers that only want a time see. For
   example, `get_game_hltb` re-raises, or returns a sentinel, behind a keyword that only `constraints` uses. A failure
   isn't cached, so the next call retries it; a real miss is cached as today. The library build (`library.add_hltb_data`)
   keeps treating both as a miss.

## Touches
`export.py`, maybe `ui.py` (sharing the price formatter), `digest.py` (only if its tables need the change), `hltb.py`,
`constraints.py`, README (export/digest lines if the format changes), tests. Not `tools.py`, `explain.py`,
`recommend.py` or `cli.toolbar` (C01 is editing those).

## Acceptance
- [ ] An exported pick shows `-75% $7.49` in one Price column; the backlog table has no price column.
- [ ] `--export` has a Wildcards table when the result has wildcards, and none when it doesn't.
- [ ] A raised HLTB search isn't cached; a no-match is; the next call retries the failure.
- [ ] Golden/structural export tests are updated deliberately, and names with `|`, `<`, `&` still don't break either format.

## Proof
Tests for each item. Live: `uv run gamer-guide --export data/output/picks.md` and `--digest` (both in your worktree);
paste the first table of each.

## Out of scope
New tools, ranking changes, the HTML styling.
