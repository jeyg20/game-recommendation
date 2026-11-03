"""Thin client for the Steam Web API (library, profile, stats) and the store `appdetails` endpoint."""

from pathlib import Path
from typing import NotRequired, TypedDict, cast

import requests

from src import config, storage
from src.ui import status, style

PLAYER_SUMMARY = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"
PLAYER_OWNED_GAMES = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
PLAYER_GAME_STATS = "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v0002"
PLAYER_ACHIEVEMENTS = "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v0001"
PLAYER_RECENT_GAMES = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v0001"
GAME_DETAILS = "https://store.steampowered.com/api/appdetails"
STORE_SEARCH = "https://store.steampowered.com/api/storesearch/"
WISHLIST = "https://api.steampowered.com/IWishlistService/GetWishlist/v1"
FRIEND_LIST = "https://api.steampowered.com/ISteamUser/GetFriendList/v0001"
PLAYER_SUMMARY_MAX_IDS = 100  # GetPlayerSummaries takes at most this many comma-separated ids

type Params = dict[str, str | int]
type SteamResponse = dict[str, object] | None


class OwnedGame(TypedDict):
    """One `GetOwnedGames` entry (with include_appinfo=1). Playtimes are in minutes."""

    appid: int
    name: str
    playtime_forever: int
    img_icon_url: str
    playtime_windows_forever: int
    playtime_mac_forever: int
    playtime_linux_forever: int
    playtime_deck_forever: int
    rtime_last_played: int
    playtime_disconnected: int
    playtime_2weeks: NotRequired[int]
    has_community_visible_stats: NotRequired[bool]
    has_leaderboards: NotRequired[bool]
    content_descriptorids: NotRequired[list[int]]


class OwnedGamesPayload(TypedDict):
    """Empty (`{}`) when the profile's game details are private."""

    game_count: NotRequired[int]
    games: NotRequired[list[OwnedGame]]


class OwnedGamesResponse(TypedDict):
    response: OwnedGamesPayload


class StoreSearchItem(TypedDict):
    """One `storesearch` hit. `type` is "app" for games (soundtracks and DLC come back too)."""

    id: int
    name: str
    type: str


class StoreApp(TypedDict):
    """One app in an `appdetails` response; `data` is the full store page payload."""

    success: bool
    data: NotRequired[dict[str, object]]  # missing when success is false


class WishlistItem(TypedDict):
    """One `GetWishlist` entry."""

    appid: int
    priority: int  # the player's own ordering on the wishlist page; 0 for every game until they reorder it
    date_added: int  # unix time


def steam_request(url: str, params: Params) -> SteamResponse:
    """GET `url`; parsed JSON on success, otherwise None (HTTP, network and JSON errors are printed, not raised)."""
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except requests.RequestException as e:
        status.say(style(f"  ! Steam request failed {url}: {redact(str(e), params)}", "yellow"))
        return None


# requests puts the full URL in its error text; key and steamid come from .env, steamids can be the friends' ids
SECRET_PARAMS = ("key", "steamid", "steamids")


def redact(message: str, params: Params) -> str:
    """`message` with the API key and Steam ID taken out, so a failed request never prints them."""
    for name in SECRET_PARAMS:
        value = str(params.get(name, ""))
        if value:
            message = message.replace(value, f"<{name}>")
    return message


def get_game_details(game_id: str) -> dict[str, StoreApp] | None:
    """Store page data for one app, keyed by the appid as a string."""
    status.update(f"Getting game {game_id} details")
    params: Params = {"appids": game_id}
    return cast(dict[str, StoreApp] | None, steam_request(GAME_DETAILS, params))


def search_store(term: str) -> list[StoreSearchItem]:
    """Store search by name, best match first. Empty on no match or a failed request."""
    return try_search_store(term) or []


def try_search_store(term: str) -> list[StoreSearchItem] | None:
    """search_store, but None on a failed request: a caller that caches misses (xbox) must not cache a hiccup."""
    data = steam_request(STORE_SEARCH, {"term": term, "cc": "us", "l": "en"})
    if data is None:
        return None
    return cast(list[StoreSearchItem], data.get("items", []))


def get_player_sumamry(steam_id: str, steam_api_key: str) -> SteamResponse:
    """Public profile summary for one SteamID."""
    status.update("Getting player summary")
    params: Params = {"key": steam_api_key, "steamids": steam_id, "format": "json"}
    return steam_request(PLAYER_SUMMARY, params)


def get_owned_games(steam_id: str, steam_api_key: str) -> OwnedGamesResponse | None:
    """Every game the account owns, with names and playtime."""
    status.update("Getting player owned games")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "format": "json", "include_appinfo": 1}
    return cast(OwnedGamesResponse | None, steam_request(PLAYER_OWNED_GAMES, params))


def get_recent_games(steam_id: str, steam_api_key: str) -> SteamResponse:
    """Games played in the last two weeks."""
    status.update("Getting player recent games")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "format": "json"}
    return steam_request(PLAYER_RECENT_GAMES, params)


def get_player_stats(steam_id: str, steam_api_key: str, game_id: str) -> SteamResponse:
    """The player's stats and achievements for one game."""
    status.update("Getting player stats")
    params: Params = {"appid": game_id, "key": steam_api_key, "steamid": steam_id, "format": "json"}
    return steam_request(PLAYER_GAME_STATS, params)


def get_player_achievements(steam_id: str, steam_api_key: str, appid: int) -> tuple[int, int] | None:
    """(achieved, total) achievements for one game. None for a game without achievements or a failed request."""
    status.update(f"Getting achievements for {appid}")
    params: Params = {"appid": appid, "key": steam_api_key, "steamid": steam_id, "format": "json"}
    data = steam_request(PLAYER_ACHIEVEMENTS, params)
    stats = data.get("playerstats") if data is not None else None
    if not isinstance(stats, dict):
        return None
    achievements = cast(list[dict[str, object]], cast(dict[str, object], stats).get("achievements") or [])
    if not achievements:
        return None
    return sum(1 for a in achievements if a.get("achieved") == 1), len(achievements)


def get_wishlist(steam_id: str, steam_api_key: str = "") -> list[WishlistItem] | None:
    """The account's wishlist. None on a failed request; empty when the wishlist is empty or private (Steam answers
    both with `{"response": {}}`). A public wishlist needs no key; the key is sent when set."""
    status.update("Getting wishlist")
    params: Params = {"steamid": steam_id}
    if steam_api_key:
        params["key"] = steam_api_key
    data = steam_request(WISHLIST, params)
    response = data.get("response") if data is not None else None
    if not isinstance(response, dict):
        return None
    return cast(list[WishlistItem], cast(dict[str, object], response).get("items", []))


def get_friend_ids(steam_id: str, steam_api_key: str) -> list[str] | None:
    """SteamIDs of the account's friends. None on a failed request (Steam answers 401 when the friends list is
    private)."""
    status.update("Getting friends list")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "relationship": "friend"}
    data = steam_request(FRIEND_LIST, params)
    friendslist = data.get("friendslist") if data is not None else None
    if not isinstance(friendslist, dict):
        return None
    friends = cast(list[dict[str, object]], cast(dict[str, object], friendslist).get("friends") or [])
    return [str(friend["steamid"]) for friend in friends if "steamid" in friend]


def get_player_names(steam_ids: list[str], steam_api_key: str) -> dict[str, str]:
    """steamid -> display name, PLAYER_SUMMARY_MAX_IDS ids per request. Ids Steam doesn't answer for are left out."""
    found: dict[str, str] = {}
    for start in range(0, len(steam_ids), PLAYER_SUMMARY_MAX_IDS):
        data = get_player_sumamry(",".join(steam_ids[start : start + PLAYER_SUMMARY_MAX_IDS]), steam_api_key)
        response = data.get("response") if data is not None else None
        players = cast(dict[str, object], response).get("players") if isinstance(response, dict) else None
        for player in cast(list[dict[str, object]], players or []):
            if isinstance(player.get("steamid"), str) and isinstance(player.get("personaname"), str):
                found[cast(str, player["steamid"])] = cast(str, player["personaname"])
    return found


# def merge_data(data_a: dict, data_b: dict):
#     pass


if __name__ == "__main__":
    owned_games = get_owned_games(config.STEAM_ID, config.STEAM_API_KEY)
    if owned_games is None:
        raise SystemExit("GetOwnedGames request failed")
    player_games = owned_games["response"]
    storage.write_json(Path("player_owned_games.json"), player_games)

    game_ommit_list = ["test", "alpha", "beta"]
    game_names_list = [
        game["name"]
        for game in player_games.get("games", [])
        if not any(omit in game["name"].lower() for omit in game_ommit_list)
    ]

    # games_achivements_dict = {}
    # null_ids = []
    # for game_id in games_appid_list:
    #     stats = get_player_stats(steam_id, game_id)
    #     if stats is not None:
    #         games_achivements_dict[f"GameID {game_id}"] = stats
    #     else:
    #         print(f"Null Data: {game_id}")
    #         null_ids.append(game_id)
    #
    # write_file("games_achivements.json", games_achivements_dict)
