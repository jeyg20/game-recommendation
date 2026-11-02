# Game Recommendation

Get game recommendations from your Steam library using Google's Gemini. Cross-references thousands of games
against a taste profile built from your own playtime.

## Usage

```sh
uv sync                     # once: installs the deps and the `gamer-guide` command into .venv
python -m src.tag_profile   # once: build the Steam-wide tag corpus (asks before paging SteamSpy, ~1h)

uv run gamer-guide              # chat with the assistant
uv run gamer-guide --report     # one-shot ranked report (saved preferences applied), no LLM
uv run gamer-guide --sessions   # list logged chat sessions
uv run gamer-guide --open 3     # print session 3 in full
```

Paths are anchored to the repo, so it runs from any directory: `uv run --project ~/dev/gamer_guide gamer-guide`,
or link `.venv/bin/gamer-guide` into `~/.local/bin`. `python main.py` still works.

Needs `STEAM_API_KEY`, `STEAM_ID` and `GEMINI_API` in `.env` (`--report` works without `GEMINI_API`).

**In the chat:** Enter sends; pasted multi-line text stays one prompt; Alt+Enter adds a line break; Tab completes
`/commands` and Steam tag names. The bottom bar shows the session and what has been noted for the next
recommendation. Picks are shown as tables straight from the ranking (fit bar, rating or hours played, matched tags);
Gemini adds a short commentary underneath. Colors turn off when output is piped or `NO_COLOR` is set.

## MCP server

The same tools are also served over MCP (`src/mcp_server.py`, stdio), so Claude Code or Claude Desktop does the
reasoning instead of Gemini; no `GEMINI_API` needed. The repo's `.mcp.json` registers it for Claude Code: open
`claude` in the repo, approve the `gamer-guide` server, and ask for picks. Elsewhere:

```sh
claude mcp add gamer-guide -- uv run --project ~/dev/gamer_guide gamer-guide-mcp
```

One server process is one session with its own log; each tool call is logged as a turn (`[mcp] <tool>`, no prompt
or reply), so `get_recommendation_history` works as in the CLI. `start_new_session` isn't served: a new
conversation is a new session. Calls run one at a time under a lock, since the tools share loaded state.
`refresh_library` and `update_background_corpus` can outlast the client's tool timeout; in Claude Code raise it with
`MCP_TOOL_TIMEOUT` (milliseconds) if they get cut off. Status messages go to the client's MCP log (stderr).

## Assistant

Gemini (`src/ai_agent.py`) reads your prompt and calls the tools in `src/tools.py`:

| Tool | What it does |
| --- | --- |
| `get_taste_profile` | Strongest profile tags, most-played games, saved preferences |
| `recommend_games` | Ranks unowned games, optionally steered: `like_games` ("more like X"), `boost_tags`, `avoid_tags` |
| `note_request` | Records what you want (games, tags, notes) over several messages, without recommending yet |
| `look_up_game` | One game's tags, whether you own it, and how well it fits your profile |
| `update_preferences` | Saves steers that apply to every future request (`data/preferences.json`) |
| `refresh_library` | Refetches the Steam library, tags and HLTB times; reports new games and new playtime |
| `update_background_corpus` | Grows the tag corpus and rebuilds the idf table (slow, asks first) |
| `get_recommendation_history` | Past requests in this session (or another, or all): how they were steered, top picks and why |
| `list_past_sessions` | Logged sessions with their first prompt |
| `start_new_session` | Closes this session's log and starts a fresh one (also `/new`) |

Steering: the library profile is scaled to unit length and the reference games' tag vectors are added on top
(sharing a weight of 1.0 between them). Boost tags are must-haves: their SteamSpy pools are always fetched, every
finalist has to be in all of them and carry each one among its 10 strongest tags (any one of them, flagged as
relaxed, when fewer than 10 picks qualify), and they get the strongest tag's weight in the ranking. Boost tags
saved with `update_preferences` are standing tastes instead: they add weight but never filter. Avoided tags are
removed from the profile, and any candidate with one among its 5 strongest is dropped. The backlog follows the same
must-have and avoid rules.

**When it recommends.** Only when you ask for picks ("recommend", "what should I play", "that's it") or send a
complete request in one message. While you're still describing what you want, it notes each detail with
`note_request`, confirms it, and asks if there's more; everything noted is applied when it does recommend.

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
