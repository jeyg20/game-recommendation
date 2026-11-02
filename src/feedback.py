"""The player's reactions to picks: liked, disliked and not-interested games, kept across sessions.

Each game keeps its tags so ranking can steer by it without another request. A game sits in at most one list.
"""

from datetime import datetime
from typing import Literal, TypedDict, cast

from src.config import FEEDBACK_PATH
from src.storage import read_json, write_json


class FeedbackGame(TypedDict):
    appid: int
    name: str
    tags: dict[str, int]
    at: str  # ISO, local time: when it was rated


class Feedback(TypedDict):  # each list keyed by appid as a string (JSON keys are strings)
    liked: dict[str, FeedbackGame]
    disliked: dict[str, FeedbackGame]
    not_interested: dict[str, FeedbackGame]


Rating = Literal["liked", "disliked", "not_interested"]
RATINGS: tuple[Rating, ...] = ("liked", "disliked", "not_interested")


def empty_feedback() -> Feedback:
    return {"liked": {}, "disliked": {}, "not_interested": {}}


def load_feedback() -> Feedback:
    if FEEDBACK_PATH.is_file():
        return cast(Feedback, read_json(FEEDBACK_PATH))
    return empty_feedback()


def save_feedback(feedback: Feedback) -> None:
    write_json(FEEDBACK_PATH, feedback)


def unrate(feedback: Feedback, appid: int) -> None:
    for rating in RATINGS:
        feedback[rating].pop(str(appid), None)


def rate(feedback: Feedback, rating: Rating, appid: int, name: str, tags: dict[str, int]) -> None:
    """Put the game in this list, taking it out of the others: rating it again moves it."""
    unrate(feedback, appid)
    feedback[rating][str(appid)] = {
        "appid": appid,
        "name": name,
        "tags": tags,
        "at": datetime.now().isoformat(timespec="seconds"),
    }


def find_rated(feedback: Feedback, name: str) -> int | None:
    """appid of a rated game by its stored name (case-insensitive), so removing one needs no store search."""
    key = name.strip().lower()
    for rating in RATINGS:
        for game in feedback[rating].values():
            if game["name"].lower() == key:
                return game["appid"]
    return None


def never_recommend(feedback: Feedback) -> set[int]:
    """Disliked and dismissed games: they never come back as picks."""
    return {game["appid"] for rating in ("disliked", "not_interested") for game in feedback[rating].values()}


def names(feedback: Feedback) -> dict[str, list[str]]:
    """rating -> game names, for showing the model."""
    return {rating: [game["name"] for game in feedback[rating].values()] for rating in RATINGS}
