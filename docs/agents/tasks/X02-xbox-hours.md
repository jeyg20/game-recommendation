# X02: Real Xbox hours (MinutesPlayed)

**Branch:** `feat/x02-xbox-hours` off `ai-created` · **Wave:** 6 · **Needs:** X01

## Goal
Weight Xbox games by the hours the player actually spent, like Steam games, and let them ask "what did I play most
on Xbox?".

## What the API gives (measured 2026-11-03, the owner's real account)
X01 assumed OpenXBL had no hours. It does, in a different endpoint:
- `GET https://xbl.io/api/v2/achievements/stats/{titleId}` (header `X-Authorization`, same key as X01) →
  `{"content": {"groups": [...], "statlistscollection": [{"arrangebyfield", "arrangebyfieldid", "stats": [...]}]}}`.
  A stat `{"name": "MinutesPlayed", "type": "Integer", "value": "8471"}` is the minutes in that game (Forza Horizon 5:
  8471 min = 141 h). One request per title. Some stat entries have no `value`: ignore those.
- Coverage: of the 47 distinct titles that are console-platform (`XboxOne` / `XboxSeries` in `devices`) or have
  achievement progress, **46 have MinutesPlayed** (671 h in total). Top: Forza Horizon 5 141 h, Forza Horizon 4 87 h,
  Oblivion Remastered 77 h, Valheim 75 h (0% achievements!), Hollow Knight 58 h, Sea of Thieves 34 h. Palworld had
  none.
- The other **112 distinct titles are `["Win32"]` entries with 0 progress, and a sample of 12 of them (Enshrouded,
  Witcher 3, Cyberpunk, V Rising...) returned no MinutesPlayed at all**: they are catalogue entries, not evidence of
  play. Don't spend requests on them.
- Limit 150 requests per hour; about 80 were used by the coordinator in the current window. **Your whole task may use
  at most 60 live requests**, and the full fetch needs ~47, so do it once, and say so if the limit is hit (HTTP 429).
- Same secret rules as X01: the key lives only in `config.XBOX_API_KEY`, never printed, logged or put in a test,
  fixture or doc. Don't print the gamertag or real name either.
- Optional signal seen: `GET /achievements/title/{titleId}` lists achievements with `progression.timeUnlocked`
  timestamps (a last-activity date). Not part of this task; don't call it.

## Today (after X01)
`xbox.py` weights an Xbox-only game as `XBOX_BASE_WEIGHT 0.05 + XBOX_PROGRESS_WEIGHT 0.6 * progress`, because it
believed there were no hours. At that weight Valheim (75 h, 0% achievements) counts as nothing and the 112 empty PC
entries still add 0.05 each.

## Design
1. `openxbl.get_minutes_played(title_id) -> int | None` (`None` on failure or no stat; never raises; reuse the client's
   header handling).
2. `xbox.py`: a cache `data/raw/xbox_minutes.json` (path in `config.py`): `{titleId: {minutes: int | null,
   fetched_at}}`. Fetch only for **candidate titles**: console-platform or progress > 0, after the same name
   dedupe/demo filter X01 uses. A `null` result is cached too (so Palworld isn't asked every time); entries are asked
   again after `MINUTES_MAX_AGE_DAYS = 7`. Pace requests (0.5 s) and cap one refresh at `MINUTES_MAX_REQUESTS = 80`
   (oldest first), so the hourly limit can't be blown by one call. A failed request (not a null result) isn't cached.
   `refresh_xbox` does the fetching; `ensure_loaded` only reads the cache, never the network (as X01).
3. **Weight**: an Xbox-only game with minutes gets the Steam rule: `tag_profile.taste_weight(hours, None, tags)` (volume
   only, there is no story time), no recency, no abandonment. A game without minutes gets **0 weight** (it is still
   matched and excluded from recommendations, as X01). Remove `XBOX_BASE_WEIGHT` / `XBOX_PROGRESS_WEIGHT` and update
   their comment and tests. Expected effect on this account: Xbox adds roughly 9 weight against ~16 for Steam, in
   proportion to real hours; report the real number.
4. **Steam duplicates**: for a game that is in the Steam library and also has Xbox minutes, **add the Xbox hours to its
   Steam playtime** when its weight is computed (the same game played on both). Pass it in as a join keyed by appid,
   like `last_played` / `achievement_share` in `tools.library_profile`. If this needs more than ~25 lines or touches
   `evaluate.py`'s numbers, skip it and say why in the hand-back. Keep `scripts/evaluate.py` Steam-only and
   byte-identical either way.
5. **Answer the question**: `refresh_xbox`'s result gains `most_played: [{name, hours}]` (top 10 by hours, only games
   with minutes) and `total_hours`; `get_taste_profile` gains `xbox_most_played` (top 5) so the model can answer "what
   did I play most on Xbox?" without another call. Put that use in the `refresh_xbox` / `get_taste_profile` docstrings
   (Claude Desktop ignores MCP server instructions).
6. Nothing set up → behaviour identical to before (no key, no caches).

## Touches
`openxbl.py`, `xbox.py`, `config.py` (one path), `tools.py` (`refresh_xbox`, `get_taste_profile`, the weights join),
`tests/test_xbox.py`, README (the Xbox paragraph: hours, not progress), the `refresh_xbox` docstring. Not
`evaluate.py`, `recommend.py` or the registries (no new tool).

## Acceptance
- [ ] With a fake client: an Xbox-only game with 75 h counts with `taste_weight(75, None, tags)`; one with no minutes
      counts 0 and is still excluded from picks.
- [ ] A cached `null` is not asked again within 7 days; an older entry is; a failed request isn't cached.
- [ ] One refresh never makes more than `MINUTES_MAX_REQUESTS` requests.
- [ ] `most_played` is sorted by hours and has at most 10 entries.
- [ ] No key, or no cache: all existing outputs unchanged; the full existing suite passes.
- [ ] The key appears nowhere (diff, tests, caches, hand-back).

## Proof
Offline tests as above. Live, in your worktree (≤ 60 requests): `scripts/mcp_smoke.py refresh_xbox` (paste counts,
`total_hours` and `most_played`), `get_taste_profile '{"top_n": 10}'` before and after (top tags and the Xbox share of
total weight), and `recommend_games '{"limit": 10}'` before and after. Second `refresh_xbox` immediately after: paste
that it made no minutes requests (count via the cache timestamps or a counter).

## Out of scope
Last-played dates, the achievements endpoint, the evaluation, Game Pass flags, the 112 PC-only entries.
