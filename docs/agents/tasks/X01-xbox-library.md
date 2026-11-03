# X01: Xbox library (via OpenXBL)

**Branch:** `feat/x01-xbox-library` off `ai-created` · **Wave:** 6 · **Needs:** C02

## Goal
Count the games the player has on Xbox / Microsoft (Game Pass, Xbox PC app, console) in the taste profile, and stop
recommending games they already have there.

## What the API gives (measured 2026-11-02, the owner's real account)
- OpenXBL (xbl.io), a third-party wrapper over Xbox Live. Base `https://xbl.io/api/v2`, header
  `X-Authorization: <key>`, key in `.env` as `XBOX_API_KEY` (`config.XBOX_API_KEY` doesn't exist yet: add it).
  Free tier: **150 requests per hour**; response header `X-RateLimit-Remaining`. The coordinator has used about 15
  already, so budget **at most 30 live requests** for your whole task.
- `GET /titles` (same list as `/achievements` and `/player/titleHistory`) →
  `{"content": {"xuid", "titles": [...], "achievementSummary"}, "code"}`. 175 entries, 165 distinct names. Each:
  `titleId, pfn, name, type ("Game"), devices (e.g. ["Win32"], ["PC","XboxSeries"]), displayImage, achievement:
  {currentAchievements, totalAchievements, currentGamerscore, totalGamerscore, progressPercentage, ...}, stats`.
  - **No hours played and no last-played date anywhere** (tried `/titles`, `/achievements`, `/player/titleHistory`,
    `/activity/history`; `/titles/history` returns a plain string). Don't invent them.
  - `totalAchievements` can be 0 while progress exists (Forza Horizon 6: 35 current, 0 total, 47%). Use
    `progressPercentage`.
  - 103 entries are `["Win32"]` with 0 progress (PC titles in the Xbox app); 40 have a console platform; 37 have
    gamerscore > 0. Only 30 names match the owner's Steam library exactly: most of the list is games the owner has
    only on Xbox / Game Pass (Witcher 3, Forza Horizon 5, Oblivion Remastered, Hades, Starfield...).
  - Demos and apps appear too ("Cursed Companions Demo", "Microsoft Minesweeper").
- Other endpoints seen: `/account`, `/player/summary` (profile: contains the real name and gamertag, never print or
  store them), `/activity/history` (20 recent achievement unlocks, `contentTitle`).
- The key is a secret: never print it, never write it to a log, a test, a fixture or the docs. The header isn't in a
  URL, but don't put the request object in an error message either.

## Today
Everything is Steam: `library.py` builds the library from Steam appids, tags come from SteamSpy by appid, and
`recommend_games` excludes owned appids. Xbox-only games aren't in the profile, and recommendations can suggest
a game the player already has on Xbox (Oblivion Remastered was a top-3 pick).

## Design
1. **Client** `src/openxbl.py`, thin like `steamspy.py` / `hltb.py`: `get_titles() -> list[XboxTitle] | None`
   (`None` on failure, never raises, `TypedDict`s). No key → `None`.
2. **Module** `src/xbox.py`:
   - `load_xbox_titles()`: cache `data/raw/xbox_titles.json` (path in `config.py`), refetched after 1 day; on a
     failed refetch use the cache of any age (OpenXBL may go down).
   - Dedupe: collapse entries with the same normalised name; drop demos/apps by name ("Demo", "Beta", "Playtest",
     "Trial") and entries with `type != "Game"`.
   - Match each title to a Steam appid by name with the same store search `tools.find_game` uses (1 request per
     title, paced; reuse its normalising and fuzzy rule). Cache every result, hit **and miss**, in
     `data/raw/xbox_matches.json` (`{titleId: {appid | null, steam_name, matched_at}}`); only unmatched or entries
     older than 30 days are asked again. A match has to be convincing (same normalised name or a high similarity):
     a wrong match is worse than none. A first run is ~150 lookups, minutes at 1 request/s: that's accepted, once.
   - Tags for matched appids via `recommend.enrich_tags` (cached corpus, fetches the rest).
   - Skip titles whose matched appid is in the Steam library: Steam has real hours.
3. **Taste weight** for the rest: no hours, so progress is the signal. `XBOX_BASE_WEIGHT = 0.2` (it is in the
   history, so it was launched) `+ XBOX_PROGRESS_WEIGHT = 0.3 * progressPercentage / 100`, range 0.2–0.5.
   Constants in `xbox.py`, tagged with why. No recency, no abandonment (no dates). Hook it in through
   `tools.library_profile` / the weights path C01 introduced, as extra `(weight, tags)` entries, default none, so the
   Steam-only profile and `scripts/evaluate.py` stay byte-identical. The evaluation does **not** include Xbox games
   (it holds out Steam games; keep its numbers comparable) and says so in its README paragraph.
4. **Exclusion:** matched Xbox appids join `excluded` in `recommend_games` (with `never_recommend`), and in
   `wildcards` / backlog paths wherever the others do, so a game the player has on Xbox isn't recommended. Say so in
   the `recommend_games` docstring.
5. **Tool** `refresh_xbox()`: refetch the titles now, match new ones, report `{titles, matched, tagged, new_matches,
   unmatched: [names, up to 20], skipped_steam_duplicates}`. Slow first call (say so in the docstring). Register in the
   four places (`TOOLS`, `mcp_server.ANNOTATIONS`, `ai_agent.TOOL_LABELS`, README table). Put the usage guidance in the
   tool's **docstring** (Claude Desktop ignores MCP server instructions): "call when the player says they play on Xbox
   or Game Pass, or after buying something on Xbox".
6. `ensure_loaded` loads the Xbox cache on every call but never does network work itself if the cache is missing:
   first use of Xbox data happens through `refresh_xbox` (or a `--xbox` CLI flag if cheaper). `get_taste_profile`
   adds `xbox_games` (count counted in the profile, `0` when not set up) so the model can say so.
7. Nothing set up (no key / no cache) → behaviour identical to today.

## Touches
New `src/openxbl.py`, `src/xbox.py`, `tests/test_xbox.py`; `config.py` (key + two paths); `tools.py`
(`library_profile` extras, `ensure_loaded`, `recommend_games` exclusion, `get_taste_profile`, new tool);
registries and README (tool table + a paragraph); `.env.example` if one exists. Not `evaluate.py` or its script.

## Acceptance
- [ ] No key, or no cache: every existing tool's output is unchanged (full existing test suite passes untouched).
- [ ] A fake client with 4 titles (one Steam duplicate, one demo, one unmatched, one matched Xbox-only): only the
      matched Xbox-only title enters the profile, with weight 0.2 + 0.3 * progress.
- [ ] A matched Xbox title never appears in `recommend_games` recommendations, wildcards or backlog.
- [ ] A miss is cached: a second `refresh_xbox` makes no store lookups for it (count the fake calls).
- [ ] OpenXBL failing mid-run keeps the old cache and reports it, no exception.
- [ ] The key appears in no output, log line, test or fixture (grep your diff and your hand-back for it).

## Proof
Offline tests with a fake client and fake store search. Live, in your worktree (≤ 30 OpenXBL requests):
`scripts/mcp_smoke.py refresh_xbox` (paste the counts, no names beyond the 20 unmatched, no gamertag/real name), then
`scripts/mcp_smoke.py get_taste_profile '{"top_n": 10}'` before and after (top tags), then
`scripts/mcp_smoke.py recommend_games '{"limit": 10}'` showing Oblivion Remastered (or any Xbox-owned game) gone.
Only `data/` inside your worktree changes.

## Out of scope
Hours or last-played (the API has neither), achievements detail, friends, store purchases, Game Pass flags, a refresh
schedule, and the evaluation.
