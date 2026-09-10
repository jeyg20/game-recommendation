import asyncio
import os

from dotenv import load_dotenv

from src import ai_agent, hltb, steam_api
from pathlib import Path

load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY") or ""
STEAM_ID = os.getenv("STEAM_ID") or 0
steam_data_file_path = Path("steam_api_data.json")
hltb_date_file_path = Path("htlb_game_data_list.json")


async def main():
    player_games = []
    if not steam_data_file_path.is_file():
        print("retriving steam user games")
        owned_games = steam_api.get_owned_games(STEAM_ID, STEAM_API_KEY)
        print("games retrieved")
        player_games = owned_games["response"]
        steam_api.write_file("steam_api_data.json", player_games["games"])
    else:
        player_games = steam_data_file_path


    game_ommit_list = ["test", "alpha", "beta", "wallpaper engine"]
    game_names_list = [
        game["name"]
        for game in player_games["games"]
        if not any(omit in game.get("name", "").lower() for omit in game_ommit_list)
    ]

    games: dict[int, dict] = {}

    if not hltb_date_file_path.is_file():

        hltb_game_data_list = {}

        print("Retriving How long to beat data of the player Owned games")
        for name in game_names_list:
            game_info = await hltb.get_game_hltb(name)

            game_name = getattr(game_info, "game_name", name)
            game_main_time = getattr(game_info, "main_story", "No data")
            game_score = getattr(game_info, "review_score", "No data")
            hltb_game_data_list[game_name] = {"Compleation_time": game_main_time, "review_score": game_score}

        steam_api.write_file("hltb_game_data_list.json", hltb_game_data_list)


    for games in player_games:


if __name__ == "__main__":
    asyncio.run(main())
