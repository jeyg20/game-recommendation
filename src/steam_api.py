"""Thin client for the Steam Web API (library, profile, stats) and the store `appdetails` endpoint."""

from pathlib import Path
from typing import NotRequired, TypedDict, cast

import requests

from src import config, storage

PLAYER_SUMMARY = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"
PLAYER_OWNED_GAMES = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
PLAYER_GAME_STATS = "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v0002"
PLAYER_RECENT_GAMES = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v0001"
GAME_DETAILS = "https://store.steampowered.com/api/appdetails"
STORE_SEARCH = "https://store.steampowered.com/api/storesearch/"

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


def steam_request(url: str, params: Params) -> SteamResponse:
    """GET `url`; parsed JSON on success, otherwise None (HTTP, network and JSON errors are printed, not raised)."""
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except requests.RequestException as e:
        print(f"Steam request failed {url}: {e}")
        return None


def get_game_details(game_id: str) -> dict[str, StoreApp] | None:
    """Store page data for one app, keyed by the appid as a string."""
    print(f"Getting game {game_id} details")
    params: Params = {"appids": game_id}
    return cast(dict[str, StoreApp] | None, steam_request(GAME_DETAILS, params))


def search_store(term: str) -> list[StoreSearchItem]:
    """Store search by name, best match first. Empty on no match or a failed request."""
    data = steam_request(STORE_SEARCH, {"term": term, "cc": "us", "l": "en"})
    if data is None:
        return []
    return cast(list[StoreSearchItem], data.get("items", []))


def get_player_sumamry(steam_id: str, steam_api_key: str) -> SteamResponse:
    """Public profile summary for one SteamID."""
    print("Getting player summary")
    params: Params = {"key": steam_api_key, "steamids": steam_id, "format": "json"}
    return steam_request(PLAYER_SUMMARY, params)


def get_owned_games(steam_id: str, steam_api_key: str) -> OwnedGamesResponse | None:
    """Every game the account owns, with names and playtime."""
    print("Getting player owned games")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "format": "json", "include_appinfo": 1}
    return cast(OwnedGamesResponse | None, steam_request(PLAYER_OWNED_GAMES, params))


def get_recent_games(steam_id: str, steam_api_key: str) -> SteamResponse:
    """Games played in the last two weeks."""
    print("Getting player recent games")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "format": "json"}
    return steam_request(PLAYER_RECENT_GAMES, params)


def get_player_stats(steam_id: str, steam_api_key: str, game_id: str) -> SteamResponse:
    """The player's stats and achievements for one game."""
    print("Getting player stats")
    params: Params = {"appid": game_id, "key": steam_api_key, "steamid": steam_id, "format": "json"}
    return steam_request(PLAYER_GAME_STATS, params)


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
