import json
import logging
from collections.abc import Mapping
from typing import TypedDict, cast

import requests

log = logging.getLogger(__name__)

STEAM_SPY_URL = "https://steamspy.com/api.php"


class GameSummary(TypedDict):
    """Fields common to every SteamSpy endpoint."""

    appid: int
    name: str
    developer: str
    publisher: str
    score_rank: str
    positive: int
    negative: int
    userscore: int
    owners: str
    average_forever: int
    average_2weeks: int
    median_forever: int
    median_2weeks: int
    price: str
    initialprice: str
    discount: str
    ccu: int


class GameDetails(GameSummary):
    """Extra fields only returned by `appdetails`."""

    languages: str
    genre: str
    tags: dict[str, int]


def steamspy_request(query_params: Mapping[str, str | int]) -> object | None:
    try:
        res = requests.get(STEAM_SPY_URL, params=query_params, timeout=10)
        res.raise_for_status()
        return res.json()
    except requests.RequestException as e:
        log.warning("SteamSpy request failed %s: %s", query_params, e)
        return None


def get_game_details(appid: int) -> GameDetails | None:
    data = steamspy_request({"request": "appdetails", "appid": appid})
    # Non-dict = failed/garbage response; null name = unknown appid stub.
    if not isinstance(data, dict) or data.get("name") is None:
        return None
    game = cast(GameDetails, data)
    if not game["tags"]:  # SteamSpy sends [] (PHP empty array) for "no tags"
        game["tags"] = {}
    return game


def get_games_by_tag(game_tag: str) -> dict[str, GameSummary] | None:
    data = steamspy_request({"request": "tag", "tag": game_tag})
    return cast(dict[str, GameSummary], data) if isinstance(data, dict) else None


def get_games_by_genre(game_genre: str) -> dict[str, GameSummary] | None:
    data = steamspy_request({"request": "genre", "genre": game_genre})
    return cast(dict[str, GameSummary], data) if isinstance(data, dict) else None


def get_games_by_page(page_num: int) -> dict[str, GameSummary] | None:
    data = steamspy_request({"request": "all", "page": page_num})
    return cast(dict[str, GameSummary], data) if isinstance(data, dict) else None


def get_top_100_games_forever() -> dict[str, GameSummary] | None:
    data = steamspy_request({"request": "top100forever"})
    return cast(dict[str, GameSummary], data) if isinstance(data, dict) else None


def get_top_100_games_two_weeks() -> dict[str, GameSummary] | None:
    data = steamspy_request({"request": "top100in2weeks"})
    return cast(dict[str, GameSummary], data) if isinstance(data, dict) else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    game_data = get_game_details(346110)
    if game_data is None:
        raise SystemExit("appdetails request failed or unknown appid")
    print(json.dumps(game_data, indent=2))

    # Schema drift check: symmetric diff between declared and actual keys.
    expected_details = set(GameDetails.__annotations__)
    for appid in (346110, 730, 570, 1245620, 2000000):
        details = get_game_details(appid)
        if details is None:
            print(appid, "-> None")
            continue
        print(appid, expected_details ^ set(details), type(details["score_rank"]).__name__)

    expected_summary = set(GameSummary.__annotations__)
    by_tag = get_games_by_tag("Roguelike")
    if by_tag:
        sample = next(iter(by_tag.values()))
        print("tag endpoint diff:", expected_summary ^ set(sample))
