# Game Recommendation

Get game recommendations from your Steam library using Google's Gemini. Cross-references thousands of games
against a taste profile built from your own playtime.

## Usage

```sh
python -m src.tag_profile   # once: build the Steam-wide tag corpus (asks before paging SteamSpy, ~1h)
python main.py              # chat with the assistant
python main.py --report     # one-shot ranked report (saved preferences applied), no LLM
python main.py --sessions   # list logged chat sessions
python main.py --open 3     # print session 3 in full
```

In the chat, Enter sends; pasted multi-line text stays a single prompt, and Alt+Enter adds a line break.

Needs `STEAM_API_KEY`, `STEAM_ID` and `GEMINI_API` in `.env` (`--report` works without `GEMINI_API`).
Run from the repo root.

## Assistant

Gemini (`src/ai_agent.py`) reads your prompt and calls the tools in `src/tools.py`:

| Tool | What it does |
| --- | --- |
| `get_taste_profile` | Strongest profile tags, most-played games, saved preferences |
| `recommend_games` | Ranks unowned games, optionally steered: `like_games` ("more like X"), `boost_tags`, `avoid_tags` |
| `look_up_game` | One game's tags, whether you own it, and how well it fits your profile |
| `update_preferences` | Saves steers that apply to every future request (`data/preferences.json`) |
| `refresh_library` | Refetches the Steam library, tags and HLTB times; reports new games and new playtime |
| `update_background_corpus` | Grows the tag corpus and rebuilds the idf table (slow, asks first) |
| `get_recommendation_history` | Past requests in this session (or another, or all): how they were steered, top picks and why |
| `list_past_sessions` | Logged sessions with their first prompt |
| `start_new_session` | Closes this session's log and starts a fresh one (also `/new`) |

Steering: the library profile is scaled to unit length, the reference games' tag vectors are added on top (sharing a
weight of 1.0 between them), boosted tags get half the strongest tag's weight, and avoided tags are removed from the
profile. Any candidate with an avoided tag among its 5 strongest is dropped.

**Sessions.** Each `python main.py` run is a session with its own log, `data/logs/sessions/<start time>.jsonl`.
A turn records the prompt, each tool call with its arguments and full result, and the reply. `/new` (or asking for
a new session) closes the log and resets the model's memory, so a new request isn't colored by the last one; saved
preferences still apply. In the chat: `/history` shows this session, `/sessions` lists all, `/open <n>` prints one.

**Backlog.** Owned games never launched or barely started: under 2 hours, and under a quarter of the HLTB main story
when there is one (so a finished 90-minute game doesn't count).

## Pipeline

1. **Library** — owned games + playtime (Steam Web API), tags/genre (SteamSpy `appdetails`, non-games dropped by
   `is_excluded`, test servers/playtests/betas dropped by name), main-story time (HLTB). Cached under `data/raw/`.
2. **Corpus / idf** — SteamSpy tags for ~4k popular games (`data/corpus/`), turned into a smoothed idf table so
   generic tags (Action, Singleplayer) weigh less than distinctive ones.
3. **Taste weight** per owned game — mean of `completion = min(playtime / main_story, 1)` and
   `volume = 1 - exp(-playtime / 40)`; volume only when there's no main story or the game is multiplayer/idle.
4. **Profile vector** — sum of `weight * vote_share * idf` over all owned games' tags. The whole dict is the vector.
5. **Candidates** — one SteamSpy `tag` request per top-N profile tag (cached for a day in `data/candidates/`),
   union, minus every owned appid.
6. **Narrow** — shovelware floor (≥500 reviews, ≥75% positive), rank by number of tag pools hit, keep 100.
7. **Score** — finalists' full tag vectors (reused from the corpus, missing ones fetched and appended to it), cosine
   against the profile with the same idf table.
8. **Backlog** — unplayed or barely started owned games scored the same way, as a separate section.
9. **Explain** — the assistant explains each pick from its matched tags (see above).

`--report` prints the ranking and saves it to `data/output/recommendations.json`.

## Open ideas

- Negative weight for early abandonment instead of a zero floor.
- Diversity: the top results can all come from one dominant sub-genre; an MMR-style re-rank would spread them out.
- `reclassify_cache` is still a stub: re-run `is_excluded` over the corpus after the exclusion rules change.

## Notes on SteamSpy usage

- Rate limits: 1 req/sec for `appdetails`, `tag`, `genre`, `top100*`; 1 req/60s for `all`.
- Only `appdetails` returns `tags`/`genre`/`languages`. `tag`, `genre`, `top100*` and `all` return the same slim
  popularity/price schema with no thematic data — there is no bulk shortcut for tags.
- `tags` comes back as a `dict[str, int]` of tag → votes, but as an empty **list** when a game has no tag data.
- Hand-typed tag strings that don't exactly match SteamSpy's vocabulary return a near-empty result set with a
  200 status, not an error.
- `genre` is far too coarse for candidate retrieval (`genre=Action` alone returned 36,918 games). Use `tag` as the
  retrieval axis.
- Data refreshes once every 24h server-side — no reason to refetch the same appid/tag more than once a day.
