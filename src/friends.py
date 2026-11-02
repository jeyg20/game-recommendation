"""The player's Steam friends and the games they own, cached for a day: friends_games ranks what they own.

Friends' names and ids stay in the cache file: status messages carry counts only, and a failed request's error text
has the ids redacted (steam_api.redact), so the only place a name leaves this module is the tool's own result.
"""

import time
from datetime import datetime, timedelta
from typing import TypedDict, cast

from src import config, steam_api
from src.config import FRIENDS_PATH
from src.storage import read_json, write_json
from src.ui import status

FRIENDS_MAX_AGE = timedelta(days=1)


class Friend(TypedDict):
    name: str
    appids: list[int] | None  # None: the library is private, or Steam didn't answer for it


class FriendsCache(TypedDict):
    fetched_at: str  # ISO datetime of the last successful fetch
    friends: dict[str, Friend]  # steamid -> friend
    game_names: dict[str, str]  # appid -> name, from the friends' GetOwnedGames answers


def load_friends() -> FriendsCache | None:
    """Friends and their libraries, from the cache when it's under a day old. None when the friends list can't be
    read (a failed request, or a private friends list); that isn't cached, so a fix shows up on the next call."""
    if FRIENDS_PATH.is_file():
        cache = cast(FriendsCache, read_json(FRIENDS_PATH))
        if datetime.now() - datetime.fromisoformat(cache["fetched_at"]) < FRIENDS_MAX_AGE:
            return cache
    cache = fetch_friends()
    if cache is not None:
        write_json(FRIENDS_PATH, cache)
    return cache


def fetch_friends() -> FriendsCache | None:
    """One friends-list request, one names request, then one library request per friend at 1 request/second."""
    ids = steam_api.get_friend_ids(config.STEAM_ID, config.STEAM_API_KEY)
    if ids is None:
        return None
    names = steam_api.get_player_names(ids, config.STEAM_API_KEY) if ids else {}
    friends: dict[str, Friend] = {}
    game_names: dict[str, str] = {}
    for n, steam_id in enumerate(ids, start=1):
        owned = steam_api.get_owned_games(steam_id, config.STEAM_API_KEY)
        status.update("reading friends' libraries", n, len(ids))  # after: get_owned_games sets its own label
        time.sleep(1)
        # a private library answers {"response": {}}; a failed request is None. Both count as private for the day
        games = owned["response"].get("games") if owned is not None else None
        friends[steam_id] = {
            "name": names.get(steam_id, f"friend {n}"),
            "appids": [game["appid"] for game in games] if games is not None else None,
        }
        game_names |= {str(game["appid"]): game["name"] for game in games or []}
    return {"fetched_at": datetime.now().isoformat(), "friends": friends, "game_names": game_names}


def owners(cache: FriendsCache) -> dict[int, list[str]]:
    """appid -> names of the friends who own it, for every game in a readable friend library."""
    owned: dict[int, list[str]] = {}
    for friend in cache["friends"].values():
        for appid in friend["appids"] or []:
            owned.setdefault(appid, []).append(friend["name"])
    return owned
