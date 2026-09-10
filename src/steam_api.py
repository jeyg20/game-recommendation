import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()
steam_api_key = os.getenv("steam_api_key")
steam_id = os.getenv("steam_id")

PLAYER_SUMMARY = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"
PLAYER_OWNED_GAMES = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
PLAYER_GAME_STATS = "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v0002"
PLAYER_RECENT_GAMES = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v0001"
GAME_DETAILS = "https://store.steampowered.com/api/appdetails"

type Params = dict[str, str | int]
type SteamResponse = dict[str, Any] | None


def steam_request(url: str, params: Params) -> dict[str, Any] | None:
    try:
        res = requests.get(url, params)
        if res.status_code == 200:
            data = res.json()
            return data
    except Exception as e:
        print(res.url)
        print(e)


def get_game_details(game_id: int) -> SteamResponse:
    print("Getting game details")
    params: Params = {"appids": game_id}
    return steam_request(GAME_DETAILS, params)


def get_player_sumamry(steam_id: int, steam_api_key: str) -> SteamResponse:
    print("Getting player summary")
    params: Params = {"key": steam_api_key, "steamids": steam_id, "format": json}
    return steam_request(PLAYER_SUMMARY, params)


def get_owned_games(steam_id: int, steam_api_key: str) -> SteamResponse:
    print("Getting player owned games")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "format": json, "include_appinfo": 1}
    return steam_request(PLAYER_OWNED_GAMES, params)


def get_recent_games(steam_id: int, steam_api_key: str) -> SteamResponse:
    print("Getting player recent games")
    params: Params = {"key": steam_api_key, "steamid": steam_id, "format": json}
    return steam_request(PLAYER_RECENT_GAMES, params)


def get_player_stats(steam_id: int, steam_api_key: str, game_id: int) -> SteamResponse:
    print("Getting player stats")
    params: Params = {"appid": game_id, "key": steam_api_key, "steamid": steam_id, "format": json}
    return steam_request(PLAYER_GAME_STATS, params)


def write_file(file_name: Path, data) -> None:
    print("Writing json file")
    file_name.parent.mkdir(parents=True, exist_ok=True)
    with open(file_name, "w") as file:
        file.write(json.dumps(data, indent=2))


# def merge_data(data_a: dict, data_b: dict):
#     pass


if __name__ == "__main__":
    owned_games = get_owned_games(steam_id, steam_api_key)
    player_games = owned_games["response"]
    write_file("player_owned_games.json", player_games)

    game_ommit_list = ["test", "alpha", "beta"]
    game_names_list = [
        game["name"]
        for game in player_games["games"]
        if not any(omit in game.get("name", "").lower() and game.get("playtime_forever") for omit in game_ommit_list)
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
