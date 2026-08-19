import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STEAM_ID = os.getenv("STEAM_ID")

PLAYER_SUMMARY = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002"
PLAYER_OWNED_GAMES = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001"
PLAYER_GAME_STATS = "https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v0002"
PLAYER_RECENT_GAMES = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v0001"


def steam_request(url, params):
    try:
        res = requests.get(url, params)
        if res.status_code == 200:
            data = res.json()
            return data
    except Exception as e:
        print(res.url)
        print(e)


def get_player_sumamry(STEAM_ID):
    params = {"key": STEAM_API_KEY, "steamids": STEAM_ID, "format": json}
    return steam_request(PLAYER_SUMMARY, params)


def get_owned_games(STEAM_ID):
    params = {"key": STEAM_API_KEY, "steamid": STEAM_ID, "format": json, "include_appinfo": 1}
    return steam_request(PLAYER_OWNED_GAMES, params)


def get_recent_games(STEAM_ID):
    params = {
        "key": STEAM_API_KEY,
        "steamid": STEAM_ID,
        "format": json,
    }
    return steam_request(PLAYER_RECENT_GAMES, params)


def get_player_stats(STEAM_ID, game_id):
    params = {
        "appid": game_id,
        "key": STEAM_API_KEY,
        "steamid": STEAM_ID,
        "format": json,
    }
    return steam_request(PLAYER_GAME_STATS, params)


def write_file(file_name, data):
    with open(file_name, "w") as file:
        file.write(json.dumps(data, indent=2))


def append_file(file_name, data):
    with open(file_name, "a") as file:
        file.write("\n" + json.dumps(data, indent=2))


if __name__ == "__main__":
    owned_games = get_owned_games(STEAM_ID)
    player_games = owned_games["response"]
    write_file("player_owned_games.json", player_games)

    games_appid_list = []
    game_ommit_list = ["test", "alpha", "beta"]

    games_appid_list = [
        game["appid"]
        for game in player_games["games"]
        if not any(omit in game.get("name", "").lower() for omit in game_ommit_list)
    ]

    games_achivements_dict = {}
    null_ids = []
    for game_id in games_appid_list:
        stats = get_player_stats(STEAM_ID, game_id)
        if stats is not None:
            games_achivements_dict[f"GameID {game_id}"] = stats
        else:
            print(f"Null Data: {game_id}")
            null_ids.append(game_id)

    append_file("games_achivements.json", games_achivements_dict)

    # print(json.dumps(player_game, indent=2))
    # v_rising_data = get_recent_games(STEAM_ID)["response"]["games"][1]
