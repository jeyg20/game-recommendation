from typing import cast

import pytest

from src import recommend as rec
from src import tools
from src.feedback import load_feedback
from src.steamspy import GameSummary
from src.tag_profile import CorpusRecord, DfTable

GAMES: dict[str, tools.ReferenceGame] = {  # the store, as find_game sees it
    "hades": {"appid": 1, "name": "Hades", "tags": {"Roguelike": 50, "Action": 30}},
    "dead cells": {"appid": 2, "name": "Dead Cells", "tags": {"Roguelike": 40, "Action": 40}},
    "rust": {"appid": 3, "name": "Rust", "tags": {"Shooter": 50, "Co-op": 30}},
    "portal 2": {"appid": 4, "name": "Portal 2", "tags": {"Puzzle": 60, "Co-op": 20}},
    "outlast": {"appid": 5, "name": "Outlast", "tags": {"Horror": 60, "Action": 10}},
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> None:
    """find_game answers from GAMES; the SteamSpy pools hold every game in GAMES that has the tag; nothing is owned."""
    monkeypatch.setattr(tools, "find_game", lambda name: GAMES.get(name.strip().lower()))

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        pools: dict[str, dict[str, GameSummary]] = {}
        for tag in tags:
            pool = {
                str(g["appid"]): cast(
                    GameSummary, {"appid": g["appid"], "name": g["name"], "positive": 900, "negative": 100}
                )
                for g in GAMES.values()
                if tag in g["tags"]
            }
            if pool:
                pools[tag] = pool
        return pools, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        by_appid = {g["appid"]: g for g in GAMES.values()}
        return {
            appid: {"appid": appid, "genre": "", "tags": by_appid[appid]["tags"], "fetched_at": "", "status": "ok"}
            for appid in appids
        }

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    profile = {"Roguelike": 1.0, "Action": 1.0, "Shooter": 1.0, "Puzzle": 1.0, "Co-op": 1.0, "Horror": 1.0}
    tools.state |= {"player_games": [], "games": {}, "df_table": df_table, "profile": profile}


def picked(result: dict[str, object]) -> set[str]:
    return {pick["name"] for pick in cast(list[dict[str, object]], result["recommendations"])}


def test_rate_games_saves_moves_and_reports_unknown_names(store: None) -> None:
    tools.rate_games(liked=["Hades"], disliked=["dead cells"])
    result = tools.rate_games(liked=["Dead Cells"], not_interested=["Rust", "No Such Game"])
    assert result == {
        "feedback": {"liked": ["Hades", "Dead Cells"], "disliked": [], "not_interested": ["Rust"]},
        "not_found": ["No Such Game"],
    }
    assert set(load_feedback()["liked"]) == {"1", "2"}  # keyed by appid, unknown name not saved


def test_rate_games_removes_by_stored_name(store: None) -> None:
    tools.rate_games(liked=["Hades"], not_interested=["Rust"])
    result = tools.rate_games(remove=["HADES", "Nothing"])
    assert result == {"feedback": {"liked": [], "disliked": [], "not_interested": ["Rust"]}, "not_found": ["Nothing"]}
    assert tools.get_feedback() == {"feedback": result["feedback"]}


def test_disliked_and_not_interested_games_never_come_back(store: None) -> None:
    assert {"Dead Cells", "Rust"} <= picked(tools.recommend_games(include_backlog=False))
    tools.rate_games(disliked=["Dead Cells"], not_interested=["Rust"])
    result = tools.recommend_games(include_backlog=False)
    assert not {"Dead Cells", "Rust"} & picked(result)
    assert result["applied"]["feedback_applied"] == {"liked": 0, "disliked": 1, "not_interested": 1}
    # without saved preferences the dislike no longer steers, but neither game comes back
    assert not {"Dead Cells", "Rust"} & picked(
        tools.recommend_games(include_backlog=False, use_saved_preferences=False)
    )


def top(result: dict[str, object]) -> list[str]:
    return list(cast(dict[str, float], result["steered_top_tags"]))


def test_liking_pulls_the_profile_toward_its_tags_and_disliking_pushes_away(store: None) -> None:
    tools.rate_games(liked=["Portal 2"])
    assert top(tools.recommend_games(include_backlog=False))[0] == "Puzzle"
    tools.rate_games(disliked=["Portal 2"])
    steered = top(tools.recommend_games(include_backlog=False))
    assert "Puzzle" not in steered and steered[-1] == "Co-op"  # Portal 2 is mostly Puzzle, a little Co-op


def test_without_feedback_recommend_games_is_unsteered(store: None) -> None:
    result = tools.recommend_games(include_backlog=False)
    assert "feedback_applied" not in cast(dict[str, object], result["applied"])
    assert result["steered_top_tags"] == tools.rounded(tools.state["profile"] or {}, top(result))
