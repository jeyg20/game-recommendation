"""The player's Steam wishlist, cached for a day: rank_wishlist ranks it, recommend_games flags picks on it."""

from datetime import datetime, timedelta
from typing import TypedDict, cast

from src import config, steam_api
from src.config import WISHLIST_PATH
from src.steam_api import WishlistItem
from src.storage import read_json, write_json
from src.ui import status

WISHLIST_MAX_AGE = timedelta(days=1)


class WishlistCache(TypedDict):
    fetched_at: str  # ISO datetime of the last successful fetch
    items: list[WishlistItem]
    names: dict[str, str]  # appid -> name, filled in by `names` and kept across refetches


def read_cache() -> WishlistCache | None:
    return cast(WishlistCache, read_json(WISHLIST_PATH)) if WISHLIST_PATH.is_file() else None


def load_wishlist() -> list[WishlistItem] | None:
    """The wishlist, from the cache when it's under a day old. None when Steam can't be reached.

    An empty answer (empty or private wishlist) isn't cached, so making the wishlist public shows up on the next call.
    """
    cache = read_cache()
    if cache is not None and datetime.now() - datetime.fromisoformat(cache["fetched_at"]) < WISHLIST_MAX_AGE:
        return cache["items"]
    items = steam_api.get_wishlist(config.STEAM_ID, config.STEAM_API_KEY)
    if items:
        old_names = cache["names"] if cache is not None else {}
        write_json(WISHLIST_PATH, {"fetched_at": datetime.now().isoformat(), "items": items, "names": old_names})
    return items


def wishlisted_appids() -> set[int]:
    """Appids on the wishlist; empty when it can't be read, so every pick reads as not wishlisted."""
    return {item["appid"] for item in load_wishlist() or []}


def store_name(appid: int) -> str | None:
    """The game's name on its Steam store page. SteamSpy leaves names blank for some new and unreleased games."""
    app = (steam_api.get_game_details(str(appid)) or {}).get(str(appid))
    data = app.get("data") if app is not None and app["success"] else None
    name = data.get("name") if data is not None else None
    return name if isinstance(name, str) and name else None


def names(appids: list[int]) -> dict[int, str]:
    """appid -> game name. Wishlist entries carry no names, so misses are looked up on the store and cached.

    A game the store doesn't answer for is left out (and asked about again next time).
    """
    cache = read_cache()
    known = cache["names"] if cache is not None else {}
    missing = [appid for appid in appids if str(appid) not in known]
    for n, appid in enumerate(missing, start=1):
        status.update("looking up wishlist game names", n, len(missing))
        name = store_name(appid)
        if name is not None:
            known[str(appid)] = name
    if missing and cache is not None:
        write_json(WISHLIST_PATH, cache | {"names": known})
    return {appid: known[str(appid)] for appid in appids if str(appid) in known}
