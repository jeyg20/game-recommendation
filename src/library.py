"""The owned library: Steam playtime, enriched with SteamSpy tags and HLTB times. Each step is cached under data/raw."""

import asyncio
import re
from typing import TypedDict

from src import config, hltb, steam_api, steamspy, storage
from src.steam_api import OwnedGame
from src.tag_profile import is_excluded, split_genres
from src.ui import status


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
    status.update("fetching your Steam library")
    owned_games = steam_api.get_owned_games(config.STEAM_ID, config.STEAM_API_KEY)
    if owned_games is None:
        raise RuntimeError("GetOwnedGames request failed (check STEAM_API_KEY / STEAM_ID)")
    if "games" not in owned_games["response"]:
        raise RuntimeError("Steam returned no games: set the profile's Game details to Public")
    return owned_games["response"]["games"]


def int_keys(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Re-key by int appid. A cache hit comes back from JSON keyed by str; a fresh build is already int."""
    return {int(appid): game for appid, game in games.items()}


# Test servers, playtests and betas are separate appids that SteamSpy tags like the full game.
TEST_BUILD_NAME = re.compile(
    r"\b(test server|playtest|technical test|testing branch|beta test|open beta|closed beta|pre-alpha)\b"
    r"|[-–]\s*beta\b|\bbeta$",
    re.IGNORECASE,
)


def is_test_build(name: str) -> bool:
    return TEST_BUILD_NAME.search(name) is not None


def to_library(player_games: list[OwnedGame]) -> dict[int, LibraryGame]:
    """appid -> LibraryGame with playtime in hours; SteamSpy/HLTB fields start empty. Test builds are left out."""
    games: dict[int, LibraryGame] = {}

    for game in player_games:
        if is_test_build(game["name"]):
            continue
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
    appids = list(games.keys())
    for n, appid in enumerate(appids, start=1):
        entry = games[appid]
        status.update(f"fetching SteamSpy tags: {entry['name']}", n, len(appids))
        steamspy_data = steamspy.get_game_details(entry["appid"])

        if steamspy_data is None or is_excluded(steamspy_data["genre"], steamspy_data["tags"]):
            games.pop(appid)
            continue

        entry["genres"] = split_genres(steamspy_data["genre"])
        entry["tags"] = steamspy_data["tags"]

    return games


async def add_hltb_data(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Fill main story hours and review score from the best HLTB name match (None when there's no match)."""
    for n, entry in enumerate(games.values(), start=1):
        status.update(f"fetching HowLongToBeat: {entry['name']}", n, len(games))
        hltb_result = await hltb.get_game_hltb(entry["name"])
        entry["main_story_hours"] = getattr(hltb_result, "main_story", None)
        entry["review_score"] = getattr(hltb_result, "review_score", None)

    return games


def load_library() -> tuple[list[OwnedGame], dict[int, LibraryGame]]:
    """(every owned game, the enriched library of real games). Cached steps are read, missing ones are built."""
    player_games = storage.load_or_build(config.STEAM_DATA_FILE_PATH, fetch_owned_games)
    games = int_keys(storage.load_or_build(config.STEAMSPY_DATA, lambda: add_steamspy_data(to_library(player_games))))
    games = int_keys(asyncio.run(storage.load_or_build_async(config.HLTB_DATA_FILE_PATH, lambda: add_hltb_data(games))))
    return player_games, games


def refresh_library() -> tuple[list[OwnedGame], dict[int, LibraryGame]]:
    """Refetch every step, ignoring the caches, then overwrite them. A failed fetch leaves the old caches in place."""
    player_games = fetch_owned_games()
    games = add_steamspy_data(to_library(player_games))
    games = asyncio.run(add_hltb_data(games))
    storage.write_json(config.STEAM_DATA_FILE_PATH, player_games)
    storage.write_json(config.STEAMSPY_DATA, games)
    storage.write_json(config.HLTB_DATA_FILE_PATH, games)
    return player_games, games
