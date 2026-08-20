import asyncio
import os

from dotenv import load_dotenv

from src import hltb, steam_api

load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY") or ""
STEAM_ID = os.getenv("STEAM_ID") or 0


async def main():
    owned_games = steam_api.get_owned_games(STEAM_ID, STEAM_API_KEY)
    player_games = owned_games["response"]

    game_ommit_list = ["test", "alpha", "beta", "wallpaper engine"]
    game_names_list = [
        game["name"]
        for game in player_games["games"]
        if not any(omit in game.get("name", "").lower() for omit in game_ommit_list)
    ]

    for name in game_names_list:
        game_info = await hltb.get_game_hltb(name)

        game_name = getattr(game_info, "game_name", name)
        game_main_time = getattr(game_info, "main_story", "No data")
        game_score = getattr(game_info, "review_score", "No data")
        print(f"Game: {game_name} Main Story: {game_main_time}, Review Score: {game_score}")


if __name__ == "__main__":
    asyncio.run(main())
