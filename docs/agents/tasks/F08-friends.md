# F08: What do my friends own?

**Branch:** `feat/f08-friends` off `ai-created` · **Wave:** 3 (optional) · **Needs:** wave 1 merged

## Goal
"What could I play with my friends?" ranks games the player's friends own (and the player doesn't) against the
profile, with who owns them. Good for co-op picks.

## Today
No friends code. Verified 2026-11-01: `ISteamUser/GetFriendList/v0001/?key&steamid&relationship=friend` works
for the owner's account (15 friends). Each friend's library is `GetOwnedGames` (public profiles only);
`steam_api.get_player_sumamry` (sic) gives display names.

## Design
- `steam_api.get_friend_ids` and reuse `get_owned_games` per friend; names via one `GetPlayerSummaries` call with
  all ids (comma-separated). Private libraries are skipped and counted.
- Cache `data/raw/friends.json` for a day: `{fetched_at, friends: {steamid: {name, appids}}}`.
- Tool `friends_games(min_friends: int = 1, limit: int = 10, boost_tags, avoid_tags)`: games owned by at least
  `min_friends` friends, not by the player, passing the same avoid rules; tags via `enrich_tags` for at most
  `FRIENDS_MAX_CANDIDATES = 60` games (most-owned first, 1 req/s for misses); score by cosine. Each pick lists the
  friends who own it.
- Docstring: slow on the first call of the day.

## Touches
`steam_api.py`, `config.py`, new `src/friends.py`, the registries, both prompts (one line), README.

## Acceptance
- [ ] Private friends don't fail the call.
- [ ] Second call within a day makes no Steam requests (tag fetches for new games aside).
- [ ] Friend names and ids never go to the logs beyond what the tool returns.

## Proof
Tests with fake friend libraries. Live: `scripts/mcp_smoke.py friends_games '{"limit": 5}'`.

## Out of scope
Friends' playtime, inviting friends, multiplayer detection beyond tags.
