"""Taste adjustments the user asked to keep across sessions, applied on top of the library profile."""

from typing import TypedDict, cast

from src.config import PREFERENCES_PATH
from src.storage import read_json, write_json


class Preferences(TypedDict):
    like_games: list[str]  # "more like X": game names, resolved to tags at recommendation time
    boost_tags: list[str]
    avoid_tags: list[str]


def load_preferences() -> Preferences:
    if PREFERENCES_PATH.is_file():
        return cast(Preferences, read_json(PREFERENCES_PATH))
    return {"like_games": [], "boost_tags": [], "avoid_tags": []}


def save_preferences(preferences: Preferences) -> None:
    write_json(PREFERENCES_PATH, preferences)


def merge(saved: list[str], extra: list[str] | None) -> list[str]:
    """saved + extra, first spelling wins, case-insensitive dedupe."""
    merged: dict[str, str] = {}
    for item in [*saved, *(extra or [])]:
        merged.setdefault(item.lower(), item)
    return list(merged.values())


def without(items: list[str], removed: list[str] | None) -> list[str]:
    drop = {item.lower() for item in removed or []}
    return [item for item in items if item.lower() not in drop]
