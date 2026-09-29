"""Collect the owned library, then enrich it with SteamSpy tags and HLTB times. Each step is cached under data/raw."""

import asyncio
from typing import TypedDict

from src import config, hltb, steam_api, steamspy, storage
from src.steam_api import OwnedGame
from src.tag_profile import is_excluded, split_genres


class LibraryGame(TypedDict):
    """One owned game as it moves through the pipeline; the None fields get filled by later steps."""

    appid: int
    name: str
    playtime_hours: float
    main_story_hours: float | None  # HLTB
    review_score: int | None  # HLTB
    genres: list[str] | None  # SteamSpy
    tags: dict[str, int] | None  # SteamSpy, tag -> votes


def fetch_owned_games() -> list[OwnedGame]:
    """The account's owned games from the Steam Web API."""
    print("retriving steam user games")
    owned_games = steam_api.get_owned_games(config.STEAM_ID, config.STEAM_API_KEY)
    print("games retrieved")
    return owned_games["response"]["games"]


def to_library(player_games: list[OwnedGame]) -> dict[int, LibraryGame]:
    """appid -> LibraryGame with playtime in hours; SteamSpy/HLTB fields start empty."""
    games: dict[int, LibraryGame] = {}

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
        }

    return games


def add_steamspy_data(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Fill genres and tags from SteamSpy; drops games SteamSpy doesn't know and non-games (`is_excluded`)."""
    for appid in list(games.keys()):
        entry = games[appid]
        steamspy_data = steamspy.get_game_details(entry["appid"])

        if steamspy_data is None or is_excluded(steamspy_data["genre"], steamspy_data["tags"]):
            games.pop(appid)
            continue

        entry["genres"] = split_genres(steamspy_data["genre"])
        entry["tags"] = steamspy_data["tags"]

    return games


async def add_hltb_data(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Fill main story hours and review score from the best HLTB name match (None when there's no match)."""
    print("Retriving How long to beat data of the player Owned games")
    for entry in games.values():
        hltb_result = await hltb.get_game_hltb(entry["name"])
        entry["main_story_hours"] = getattr(hltb_result, "main_story", None)
        entry["review_score"] = getattr(hltb_result, "review_score", None)

    return games


async def main() -> None:
    player_games = storage.load_or_build(config.STEAM_DATA_FILE_PATH, fetch_owned_games)
    games = storage.load_or_build(config.STEAMSPY_DATA, lambda: add_steamspy_data(to_library(player_games)))
    games = await storage.load_or_build_async(config.HLTB_DATA_FILE_PATH, lambda: add_hltb_data(games))


if __name__ == "__main__":
    asyncio.run(main())
