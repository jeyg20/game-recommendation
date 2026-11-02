"""Did past picks work? Every game ever recommended, joined with the library as it is now.

Recommended games were unowned when picked (owned games are excluded), so owning one now means it was bought since,
and its total playtime is playtime since the pick. Backlog picks were already owned: they count as started when their
playtime has grown past the hours logged with the pick. Reads the session logs only; writes nothing.
"""

from typing import TypedDict, cast

from src.history import list_sessions, load_session
from src.library import LibraryGame

PLAYED_HOURS = 2.0  # a bought pick played past this many hours counts as a hit, not just a purchase


class PickOutcome(TypedDict):
    appid: int
    name: str
    first_recommended: str  # timestamp of the turn that first recommended it
    session: str
    rank: int  # 1-based position in that first recommendation
    score: float  # fit score at that first recommendation
    bought: bool  # owned now
    hours: float  # total playtime now; it was 0 when picked


class BacklogOutcome(TypedDict):
    appid: int
    name: str
    first_recommended: str
    session: str
    hours_then: float  # playtime logged with the first pick
    hours: float  # playtime now
    started: bool  # playtime grew past hours_then


class Outcomes(TypedDict):
    picks: list[PickOutcome]  # each recommended game once, first recommendation's rank and score, oldest first
    backlog: list[BacklogOutcome]  # each backlog pick once, excluding games also recommended as purchases


def logged_picks(picks: object) -> list[dict[str, object]]:
    """The logged picks that carry an appid. Older or failed calls may hold something else."""
    if not isinstance(picks, list):
        return []
    return [pick for pick in cast(list[object], picks) if isinstance(pick, dict) and isinstance(pick.get("appid"), int)]


def pick_outcomes(games: dict[int, LibraryGame]) -> Outcomes:
    """Walk every session's recommend_games calls, oldest first, and join each game's first pick with `games`."""
    picks: dict[int, PickOutcome] = {}
    backlog: dict[int, BacklogOutcome] = {}
    for summary in list_sessions():
        for turn in load_session(summary["session"]):
            for call in turn["tool_calls"]:
                result = call["result"]
                if call["name"] != "recommend_games" or not isinstance(result, dict):
                    continue
                result = cast(dict[str, object], result)
                for rank, pick in enumerate(logged_picks(result.get("recommendations")), start=1):
                    appid = cast(int, pick["appid"])
                    if appid in picks:
                        continue
                    owned = games.get(appid)
                    picks[appid] = {
                        "appid": appid,
                        "name": str(pick.get("name", "")),
                        "first_recommended": turn["timestamp"],
                        "session": summary["session"],
                        "rank": rank,
                        "score": float(cast(float, pick.get("score", 0.0))),
                        "bought": owned is not None,
                        "hours": owned["playtime_hours"] if owned is not None else 0.0,
                    }
                for pick in logged_picks(result.get("backlog")):
                    appid = cast(int, pick["appid"])
                    if appid in backlog:
                        continue
                    owned = games.get(appid)
                    hours_then = float(cast(float, pick.get("playtime_hours", 0.0)))
                    hours = owned["playtime_hours"] if owned is not None else hours_then
                    backlog[appid] = {
                        "appid": appid,
                        "name": str(pick.get("name", "")),
                        "first_recommended": turn["timestamp"],
                        "session": summary["session"],
                        "hours_then": hours_then,
                        "hours": hours,
                        "started": round(hours, 1) > hours_then,  # the log rounds to 0.1 h
                    }
    # a pick bought after being recommended can come back as a backlog pick; it already counts as a purchase
    return {"picks": list(picks.values()), "backlog": [b for b in backlog.values() if b["appid"] not in picks]}
