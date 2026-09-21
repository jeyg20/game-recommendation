import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from src import ai_agent, hltb, steam_api, steamspy

load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY") or ""
STEAM_ID = os.getenv("STEAM_ID") or ""
STEAM_DATA_FILE_PATH = Path("data/raw/steam_api_data.json")
HLTB_DATA_FILE_PATH = Path("data/raw/hltb_game_data_list.json")
STEAMSPY_DATA = Path("data/raw/steamspy_data.json")
OMITTED_NAME_FRAGMENTS = ["test", "alpha", "beta", "wallpaper engine"]


async def main():
    player_games = []
    if not STEAM_DATA_FILE_PATH.is_file():
        print("retriving steam user games")
        owned_games = steam_api.get_owned_games(STEAM_ID, STEAM_API_KEY)
        print("games retrieved")
        player_games = owned_games["response"]["games"]
        steam_api.write_file(STEAM_DATA_FILE_PATH, player_games)
    else:
        with open(STEAM_DATA_FILE_PATH) as file:
            player_games = json.load(file)

    games: dict[int, dict] = {}

    for game in player_games:
        appid = game["appid"]
        games[appid] = {
            "appid": appid,
            "name": game["name"],
            "playtime_hours": game["playtime_forever"] / 60,
            "main_story_hours": None,
            "review_score": None,
            "genres": None,
            "tags": None,
            "categories": None,
        }

    if not STEAMSPY_DATA.is_file():
        for appid in list(games.keys()):
            entry = games[appid]
            steamspy_data = steamspy.get_game_details(entry["appid"])

            if not steamspy_data or not steamspy_data["genre"]:
                games.pop(appid)
                continue

            genres = steamspy_data["genre"].split(", ")
            if any("utilities" in g.lower() for g in genres):
                games.pop(appid)
                continue

            entry["genres"] = genres
            entry["tags"] = steamspy_data["tags"]

        steam_api.write_file(STEAMSPY_DATA, games)
    else:
        with open(STEAMSPY_DATA) as file:
            games = json.load(file)

    if not HLTB_DATA_FILE_PATH.is_file():
        print("Retriving How long to beat data of the player Owned games")
        for entry in games.values():
            hltb_result = await hltb.get_game_hltb(entry["name"])
            entry["main_story_hours"] = getattr(hltb_result, "main_story", None)
            entry["review_score"] = getattr(hltb_result, "review_score", None)

        steam_api.write_file(HLTB_DATA_FILE_PATH, games)
    else:
        with open(HLTB_DATA_FILE_PATH) as file:
            games = json.load(file)


if __name__ == "__main__":
    asyncio.run(main())
