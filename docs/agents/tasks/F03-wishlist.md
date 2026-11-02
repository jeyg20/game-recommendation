# F03: Wishlist

**Branch:** `feat/f03-wishlist` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
"Which game on my wishlist should I get first?" ranks the wishlist against the profile, and normal picks say when
they're already wishlisted.

## Today
- No wishlist code. Verified 2026-11-01: `GET https://api.steampowered.com/IWishlistService/GetWishlist/v1/
  ?steamid=<STEAM_ID>` (no key needed for a public wishlist) returns `{"response": {"items": [{"appid",
  "priority", "date_added"}]}}`; the owner's account has 19 items.
- `recommend.enrich_tags(appids)` returns corpus records with full tags (fetching misses at 1 req/s).
- `recommend.score(...)` scores one game; `tools.compact` shapes picks for the model.

## Design
- `steam_api.get_wishlist(steam_id) -> list[WishlistItem] | None` (TypedDict for the item), cached for a day in
  `data/raw/wishlist.json` (`{fetched_at, items}`).
- Tool `rank_wishlist(limit: int = 10)`: wishlist appids → `enrich_tags` → `score` against the library profile
  (unsteered), best first, with `priority`. Names come from the corpus record, or `steamspy.get_game_details` when
  missing. Report items that couldn't be tagged.
- In `recommend_games`: each pick gets `on_wishlist: bool` (one wishlist read per call, cached).

## Touches
`steam_api.py`, `config.py`, new code in `tools.py` (or `src/wishlist.py`), `tools.compact` (one field), the
registries, both prompts (one line), README.

## Acceptance
- [ ] Private wishlist or failed request: the tool says so instead of raising.
- [ ] `on_wishlist` is false everywhere when the wishlist can't be read.
- [ ] Second call within a day makes no wishlist request.

## Proof
Tests with a fake wishlist and fake corpus records. Live: `scripts/mcp_smoke.py rank_wishlist '{"limit": 5}'`.

## Out of scope
Prices (F04), adding to the wishlist, steering by the wishlist.
