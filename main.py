import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from src import ai_agent, hltb, steam_api

load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY") or ""
STEAM_ID = os.getenv("STEAM_ID") or 0
STEAM_DATA_FILE_PATH = Path("data/raw/steam_api_data.json")
HLTB_DATA_FILE_PATH = Path("data/raw/hltb_game_data_list.json")
STEAMSTORE_DATA = Path("data/raw/steamstore_data.json")
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
            "categories": None,
        }

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

    if not STEAMSTORE_DATA.is_file():
        for appid in list(games.keys()):
            entry = games[appid]
            steamstore_data = steam_api.get_game_details(entry["appid"])
            game_details = steamstore_data[str(entry["appid"])]

            data = game_details.get("data") if game_details.get("success") else None
            is_real = data and (
                data.get("detailed_description") or data.get("about_the_game") or data.get("short_description")
            )
            is_utility = (
                data
                and data.get("genres")
                and any("utilities" in item["description"].lower() for item in data.get("genres"))
            )

            if not is_real or is_utility:
                games.pop(appid)
                continue

            entry["genres"] = data.get("genres", [])
            entry["categories"] = data.get("categories", [])

        steam_api.write_file(STEAMSTORE_DATA, games)
    else:
        with open(STEAMSTORE_DATA) as file:
            games = json.load(file)


if __name__ == "__main__":
    asyncio.run(main())
