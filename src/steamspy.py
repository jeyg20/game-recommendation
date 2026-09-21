import json
from collections.abc import Mapping
from typing import Any, TypedDict

import requests

STEAM_SPY_URL = "https://steamspy.com/api.php?request"


class GameSummary(TypedDict):
    """One game as returned inside the tag/genre listings."""

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
    genre: str
    tag: dict[str, int]


class GameDetails(GameSummary):
    """Same as GameSummary plus the extra fields that appdetails adds."""

    languages: str
    genre: str
    tags: dict[str, int]


def steamspy_request(query_params: Mapping[str, str | int]) -> Any | None:
    try:
        res = requests.get(STEAM_SPY_URL, params=query_params, timeout=10)
        res.raise_for_status()
        return res.json()
    except requests.RequestException as e:
        print(e)
        return None


def get_game_details(appid: int) -> GameDetails | None:
    query_params = {"request": "appdetails", "appid": appid}
    return steamspy_request(query_params)


def get_game_by_tag(gameTag: str) -> dict[str, GameSummary] | None:
    query_params = {"request": "tag", "tag": gameTag}
    return steamspy_request(query_params)


def get_game_by_genre(gameGenre: str) -> dict[str, GameSummary] | None:
    query_params = {"request": "genre", "genre": gameGenre}
    return steamspy_request(query_params)


if __name__ == "__main__":
    arkse_data = get_game_details(1030300)
    print(json.dumps(arkse_data, indent=2))
    expected = set(GameDetails.__annotations__)
    # for appid in (346110, 730, 570, 1245620, 2000000):
    #     data = get_game_details(appid)
    #     if data:
    #         print(appid, expected ^ set(data), type(data["tags"]).__name__, type(data["score_rank"]).__name__)
